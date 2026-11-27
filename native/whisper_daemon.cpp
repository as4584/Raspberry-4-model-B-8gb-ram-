#include <cctype>
// Resident whisper for phone turns (#28). Loads the model once, then per stdin line
//   <raw s16le 16 kHz mono path>\t<audio_ctx>\t<max_tokens>
// prints exactly one line: the transcript (newlines removed), or "ERR <reason>".
// Greedy, no temperature fallback, single segment, and max_tokens bounds the decode
// so a non-terminating decode can't run to the 224-token limit (~4 s on a Pi 4).
#include "whisper.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

static bool read_pcm(const std::string & path, std::vector<float> & out) {
    FILE * f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::vector<int16_t> buf(4096);
    out.clear();
    size_t n;
    while ((n = std::fread(buf.data(), sizeof(int16_t), buf.size(), f)) > 0)
        for (size_t i = 0; i < n; i++) out.push_back(buf[i] / 32768.0f);
    std::fclose(f);
    return true;
}

int main(int argc, char ** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: %s <model> <threads>\n", argv[0]); return 2; }
    whisper_log_set([](enum ggml_log_level, const char *, void *) {}, nullptr);
    whisper_context_params cp = whisper_context_default_params();
    whisper_context * ctx = whisper_init_from_file_with_params(argv[1], cp);
    if (!ctx) { std::fprintf(stderr, "cannot load model\n"); return 3; }
    const int threads = std::atoi(argv[2]);
    std::printf("READY\n"); std::fflush(stdout);

    std::string line;
    while (std::getline(std::cin, line)) {
        std::string path, ac, mt;
        size_t a = line.find('\t'), b = line.find('\t', a == std::string::npos ? a : a + 1);
        if (a == std::string::npos || b == std::string::npos) { std::printf("ERR bad request\n"); std::fflush(stdout); continue; }
        path = line.substr(0, a); ac = line.substr(a + 1, b - a - 1); mt = line.substr(b + 1);
        // Optional 4th field: language ("en", "es"); a multilingual model needs it, base.en ignores it.
        std::string lang = "en";
        bool use_prompt = false;                  // optional 5th field "p": use the vocabulary prompt
        size_t c = mt.find('	');
        if (c != std::string::npos) { lang = mt.substr(c + 1); mt = mt.substr(0, c); }
        size_t d = lang.find('	');
        if (d != std::string::npos) { use_prompt = lang.substr(d + 1) == "p"; lang = lang.substr(0, d); }
        std::vector<float> pcm;
        if (!read_pcm(path, pcm) || pcm.empty()) { std::printf("ERR no audio\n"); std::fflush(stdout); continue; }

        whisper_full_params p = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
        p.n_threads = threads; p.language = lang.c_str(); p.translate = false;
        p.no_context = true; p.single_segment = std::getenv("WD_SINGLE") != nullptr;
        // Timestamps are left on as in whisper-cli: no_timestamps changes decoding, not just printing.
        p.no_timestamps = std::getenv("WD_NO_TS") != nullptr;
        p.print_progress = false; p.print_realtime = false; p.print_timestamps = false; p.print_special = false;
        p.temperature_inc = 0.0f;                 // no fallback
        p.greedy.best_of = 1;
        p.audio_ctx = std::atoi(ac.c_str());
        p.max_tokens = std::atoi(mt.c_str());
        p.suppress_nst = std::getenv("WD_SNS") != nullptr;
        // Optional vocabulary prompt per language (WD_PROMPT_EN / WD_PROMPT_ES), only when the request asks
        // for it: it fixes short turns ("A las diez" was "Alas pies") but slows long ones and loops them.
        std::string key = "WD_PROMPT_" + lang;
        for (char & ch : key) ch = (char) std::toupper((unsigned char) ch);
        const char * prompt = std::getenv(key.c_str());
        if (use_prompt && prompt && *prompt) p.initial_prompt = prompt;

        // Fresh decoder state per utterance: reusing one state across calls with a
        // changing audio_ctx made identical audio decode differently (Pi, 2026-11-18).
        whisper_state * st = whisper_init_state(ctx);
        if (!st || whisper_full_with_state(ctx, st, p, pcm.data(), (int) pcm.size()) != 0) {
            if (st) whisper_free_state(st);
            std::printf("ERR decode\n"); std::fflush(stdout); continue;
        }
        std::string text;
        for (int i = 0; i < whisper_full_n_segments_from_state(st); i++) text += whisper_full_get_segment_text_from_state(st, i);
        whisper_free_state(st);
        for (char & c : text) if (c == '\n' || c == '\r') c = ' ';
        std::printf("%s\n", text.c_str()); std::fflush(stdout);
    }
    whisper_free(ctx);
    return 0;
}
