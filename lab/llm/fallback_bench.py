"""Time a small local model answering off-script caller questions from business facts only."""
import json, subprocess, sys, time, urllib.request

FACTS = ("You are the phone receptionist for LexMakesIt, a small business in Paterson, New Jersey, run by Alex. "
         "Facts: websites start at one thousand dollars plus ten dollars a month for management. 50 percent deposit, no payment plans. "
         "No contract, cancel anytime, site turns off at month end, no refunds. Client owns the domain; Alex owns the code, which can be bought. "
         "A simple site takes about 24 hours, bigger ones up to a month. Alex builds software and hardware. No portfolio is shared. "
         "Any business can buy an AI receptionist like you; price is flexible by business size. "
         "Rules: answer in one or two short, warm sentences for a phone call. Only use the facts. "
         "If the facts do not answer it, say Alex can answer that on a free call. Never invent prices, dates or promises.")
QS = ["Do you guys build mobile apps too?", "Can you make my bakery show up on Google Maps?",
      "My nephew says websites are free now with AI, why pay you?", "Is Alex nice to work with?",
      "Can you fix my existing WordPress site?", "Do you work with businesses in New York?",
      "What happens if my site gets hacked?", "Can you put my menu online?"]


def ask(port, q):
    body = {"prompt": f"<|im_start|>system\n{FACTS}<|im_end|>\n<|im_start|>user\n{q}<|im_end|>\n<|im_start|>assistant\n",
            "n_predict": 48, "temperature": 0.2, "cache_prompt": True, "stop": ["<|im_end|>"]}
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/completion",
                  data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=120))
    return time.time() - t, r["content"].strip(), r.get("timings", {})


for model in sys.argv[1:]:
    port = 8188
    srv = subprocess.Popen(["nice", "-n", "10", "/home/prototype/opt/llama.cpp-v1.0.0/build/bin/llama-server", "-m", model,
                            "-t", "3", "-c", "1024", "--port", str(port), "--host", "127.0.0.1"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1); break
        except Exception:
            time.sleep(1)
    ask(port, "Hi")                       # warm the system prompt cache
    times = []
    print("==", model.split("/")[-1])
    for q in QS:
        dt, a, tm = ask(port, q)
        times.append(dt)
        print(f"{dt:5.2f}s | {q} -> {a}")
    times.sort()
    print(f"p50 {times[len(times)//2]:.2f}s  max {times[-1]:.2f}s")
    srv.kill(); srv.wait()
