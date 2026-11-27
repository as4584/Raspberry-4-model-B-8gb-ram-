"""The receptionist's brain: tiny local model + conversation manager (no cloud, no LLM).

- A ~23 MB sentence encoder (MiniLM-L6, int8 ONNX) + a linear head says WHAT the caller
  wants (~130 ms on the Pi). It never writes dates, JSON or replies.
- parse_when() pulls the day / time / part of day out of the words, including partial
  phrases ("how about tomorrow?", "two in the afternoon", "make that eleven").
- A per-call state remembers what's missing, offers really-free slots from the calendar,
  and hands complete requests to Dialogue, which still validates and confirms bookings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from receptionist import datetime_resolve as dr
from receptionist.booking import BookingStore
from receptionist.dialogue import ANYTHING_ELSE, DEFAULT_HOURS, OPENERS, Dialogue, Reply
from receptionist.profile import answer as profile_answer
from receptionist.speech_format import spoken_time, spoken_when

AFFIRM = re.compile(r"^(yes|yeah|yep|yup|yo|sure|correct|right|that'?s right|sounds? good|perfect|ok(ay)?|please do)\b")
NEGATE = re.compile(r"^(no|nope|nah|never ?mind|cancel that|not really)\b")
PARTS = {"morning": (time(0), time(12)), "afternoon": (time(12), time(17)), "evening": (time(17), time(23, 59))}
PREFERRED_HOURS = (10, 14, 11, 15, 9, 13, 16, 12)        # spread offers across the day
DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MIN_CONFIDENCE = 0.45
# Same meaning, different words: a person doesn't open every reply with "Sure."
VARIANTS = {"Sure.": ("Sure.", "Of course.", "Absolutely.", "Happy to help."),
            "Okay.": ("Okay.", "Alright.", "Got it."),
            "Hmm, okay.": ("Hmm, okay.", "Ah, okay.", "I see."),
            "Great, you're all set.": ("Great, you're all set.", "Perfect, you're all booked.", "Wonderful, you're all set."),
            "Okay, let me check that.": ("Okay, let me check that.", "Let me take a look.", "Sure, let me check.")}
# Cue words: the tiny model may only take drastic routes when the words support it
# (Pi suite: "Yeah." -> human, "Perfect." -> emergency, "Tomorrow is fine." -> injection).
CUES = {"injection": re.compile(r"\b(ignore|pretend|override|admin|system prompt|instructions|rules|jailbreak|developer mode)\b"),
        "human": re.compile(r"\b(someone|somebody|person|human|operator|representative|staff|manager|receptionist|real|alex|owner)\b"),
        "emergency": re.compile(r"\b(emergency|urgent|hurt|injur|bleed|pain|911|ambulance|right now)\w*"),
        "cancel": re.compile(r"\bcancel")}
BOOK_WORDS = re.compile(r"\b(book|appointment|schedule|reserve|come in|slot|opening|availability|available)\b")
SMALL_TALK = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))?\W*(there\W*)?(how are you|how's it going|how are things)|^(hi|hello|hey)( there)?$")
GOODBYE = re.compile(r"^(yeah |yep |no |nope |ok(ay)? |um |uh )*that'?s it( for me)?( thanks?( you)?| thank you)?$|\b(that'?s everything|that'?s it for now|nothing else|i'?m all set|talk (to you )?soon|take care|see ya|see you (later|soon)|bye|goodbye|thanks,? by|thank you,? by|that's all|that is all|that'll be all|have a (good|nice|great) day|bye bye|god bless|(i )?(gotta|got to|have to|need to) (go|run|hop off|jump)|gotta run|i'?ll call (you )?back|i'?ll try (you )?(again )?later|i('?ll| will) revert|peace( out)?$|^(ok(ay)?,? )?later$|catch you later|"
                     r"have a blessed day|be blessed)\b")


def closing(low: str) -> bool:
    """A goodbye that ends what they said. A "bye" to someone in the room, followed by
    more talking, hung up on a real call."""
    found = list(GOODBYE.finditer(low))
    return bool(found) and (len(low.split()) <= 6 or len(low[found[-1].end():].split()) <= 3)


ORDINALS = {"first": 0, "earliest": 0, "1st": 0, "second": 1, "2nd": 1, "middle": 1, "third": 2, "3rd": 2,
            "last": -1, "latest": -1}
# whisper writes "three" as "free"/"pre"/"tree" (Pi suite); only rewrite when a time is
# expected (times were just offered or a day is chosen) and a time word follows.
# ... also glued to "o clock" ("Preo clock"). The "o" stays so "o clock" still parses.
THREE_MISHEARD = re.compile(r"\b(free|pre|tree|pretty)(?=o[\s'-]*clock\b|\b[\s-]*(o[\s'-]*clock|pm|p m|am|a m|works|is good|sounds|please))")
MISHEARD = [(re.compile(p, re.I), w) for p, w in (
    (r"\b(into|in to|to) ?more\b(?! than)", "tomorrow"), (r"\bto morrow\b", "tomorrow"),
    (r"\bpoint ?meant\b", "appointment"), (r"\bhour\b(?= *\?| on| today)", "hours"),
    (r"\b(puff?ake|pasaic|passaick|passay it|pass a ick)\b", "Passaic"))]
DATE_Q = re.compile(r"\bwhat('?s| is)? (the )?(day|date|time)( is it| today)?\b|\btoday'?s date\b|\bwhat day is (it|today)\b")
UNKNOWN_MATCH = 0.6           # an unplaced question takes a checked answer only when it's a fair match
STRONG_MATCH = 0.75          # override a keyword FAQ hit only when clearly closer (Pi: good 0.85+, wrong 0.61)
# A question ABOUT the receptionist ("am I talking to a real person?") is not a transfer request.
IDENTITY_Q = re.compile(r"\b(am i (talking|speaking) (to|with)|are you|is (this|it|that)|was that)\b.*\b(real|person|human|robot|bot|machine|computer|ai|a i|recording)\b")
AVAIL_Q = re.compile(r"\bwhat (times|days|slots) do you (have|guys have)\b|\bwhen can i come in\b|\bwhen (are|is) (you|alex|someone|anyone|somebody)( guys)? (free|available|around)\b|\bare you (free|available) (on|at|this|next|tomorrow)\b")
TALK_TO = re.compile(r"\b(speak|talk) (with|to) (alex|the owner|someone|somebody|anyone|a person|a human|a real person)\b|\b(can|could) i (get|reach) (alex|the owner)\b")
REAL_PERSON = ("I'm LexMakesIt's A I receptionist. Alex built me, and I can answer questions or book you a free call with Alex.")
ROBOCALL = re.compile(r"\b(extended (auto |car |vehicle )?warranty|(car|auto|vehicle)'?s? (extended |factory )?warranty|"
                      r"press (1|one)\b|final notice|free cruise|you'?ve been (selected|chosen)|this is the irs|"
                      r"lawsuit (has been )?(filed|against)|lower your (credit card )?interest|"
                      r"social security (number )?(has been )?suspended|medicare (benefits|card)|"
                      r"student loan forgiveness|been trying to reach you about)", re.I)
SPAM_SPEECH = "Thanks, but we're not interested. Please remove this number from your list. Goodbye."
NOWHERE_SPEECH = ("I'm sorry, I'm having trouble understanding, so I'll let you go for now. "
                  "Please call back anytime. Goodbye.")
UNPRODUCTIVE = {"repeat", "unknown", "lost", "ask_again", "confirm_again"}
PRODUCT_Q = re.compile(r"\b(answer|pick up|take|handle) ((my|our|the) )?(phone|phones|calls?)\b(?! (with|from) alex)|"
                       r"\b(receptionist|something|someone|one) like you\b|"
                       r"\b(do|set up|make|build) (this|that|one of these|something like this) for (my|our)\b|"
                       r"\b(can|could|would|will|does|do) (it|she|this|that|a receptionist) (\w+ ){0,4}?(book|fill|handle|schedule|cancel|take (calls|messages|orders|appointments|bookings|payments)|answer|text|screen)\w*\b|"
                       r"\b(calls?|phones?) for (my|our) (business|shop|salon|restaurant|office|store|company)\b", re.I)
HAGGLE = re.compile(r"\b(knock|bring|take) (the price|it|that) down\b|\b(cheaper|discount|lower (the |your )?price|"
                    r"too expensive|for less|best price|deal on)\b|\b(do it|do that|go) for \$?\d{2,}|\bfor (\w+ )?hundred( dollars| bucks)?\b|\bcan you do (\w+ )?(hundred|\d{3})\b", re.I)
HAGGLE_SPEECH = ("I understand, and the price matters. Websites start at one thousand dollars, and that includes building it right "
                 "and keeping it running. On a free call, Alex can look at what you really need and keep it within your budget.")
SOLO_Q = re.compile(r"\b(just (alex|him|one (guy|person|man))|one (guy|man) (show|operation)|hit by a bus|"
                    r"how (big|many people))\b", re.I)
SOLO_SPEECH = ("LexMakesIt is Alex's business, so you work with him directly, the person who actually builds it. "
               "Your domain is always yours, and you can buy the code if you ever want it.")
CARD_PAY = re.compile(r"\b(customers?|clients?|people|they)\b.*\b(pay|paying)\b.*\b(card|cards|visa|mastercard|online|apple pay)\b|"
                      r"\b(accept|take) (credit )?cards?\b|\bvisa\b|\bmastercard\b", re.I)
CARD_PAY_SPEECH = ("Yes, Alex can set up your website so customers pay by card online, with Visa, Mastercard and the rest. "
                   "He can go over the options on a free call.")
RECEPTIONIST_PRICE = ("Any business can get a receptionist like me, and the price is flexible, based on your size and how many "
                      "calls you get. Great service, at a great deal. I can book you a free call with Alex to set it up.")
RECEPTIONIST_SETUP = ("Alex sets it up for you, with your business details, hours and questions, and he'll give you a "
                      "timeline on the free call. You don't need to do anything technical.")
WANTS_BOOKING = re.compile(r"\b(book|appointment|schedul\w*|reserve|set up a (call|time|meeting)|call with alex|meet(ing)?|"
                           r"available|availability|free call|slot|opening|open|free|come in|see alex|talk (to|with) (alex|someone|somebody)|"
                           r"consultation|what (do you have|times|days)|when (are|is|can|could)|let'?s (do|go|set)|go over|"
                           r"sounds good|works for me|is fine|that works|lessons?|sessions?)\b")
POLICY = [
    (re.compile(r"\b(money back|refund\w*|get (my|a) refund)\b"),
     "There's no contract, so you can cancel anytime, and your website stays up until the end of that month. "
     "We don't offer refunds, which is why the first call is free, so you can be sure before you start."),
    (re.compile(r"\bwhy\b.*\bdeposit\b|\bdeposit\b.*\bwhy\b|\bwhat'?s the deposit for\b"),
     "The deposit gets your project started, and the rest is due when it's done. "
     "On the free call, Alex walks you through exactly what you'll get first."),
    (re.compile(r"\b(recorded|recording|record (this|the|my) calls?|train(ed|ing)? (the |an |any )?(ai|a i|model)|used to train)\b"),
     "Calls aren't shared or sent to the cloud, and they're not used to train any outside AI. "
     "I run on a small computer in the office, so everything stays local."),
    (re.compile(r"\b(contract|nda|confidentiality agreement|locked in|cancel any ?time|can i cancel|if i cancel|do i own|own (it|the site|the website))\b"),
     "There's no contract, so you can cancel anytime, and your site stays up until the end of that month. "
     "You own your domain, and you can buy the code if you want it. Alex can also talk through a confidentiality agreement on the free call."),
    (re.compile(r"\b(how much|price|pricing|cost|charge|charges|rate)\b.*\b(firm|office|practice|company|business|gym|salon|shop|restaurant)\b"),
     "Websites start at one thousand dollars, plus ten dollars a month. A receptionist like me is priced for each business, "
     "based on your size, so it's flexible. Alex can give you an exact number on a free call."),
]
ROUND8 = [
    (re.compile(r"\b(pharmacy|prescription|refill|pills?|doctor'?s office|wrong number|is this (the )?(bank|hospital|pizza|cvs|walgreens|rite aid))\b"),
     "Oh, I'm sorry, this isn't the pharmacy. This is LexMakesIt, a small business that builds websites. "
     "Your pharmacy's number should be on your pill bottle or your receipt. Is there anything else I can help you with?"),
    (re.compile(r"\b(liab\w*|legal advice|medical advice|says? something wrong|gives? (someone |them )?(wrong|bad))\b"),
     "That's a good question. I only share the facts Alex gives me, and anything I'm not sure about goes to Alex. "
     "I never give legal or medical advice. Alex can go over the details with you on a free call."),
    (re.compile(r"\b(two|2) (parts|payments|installments)\b|\bsplit (the|my) deposit\b|\bhalf (now|up front)\b.*\bhalf\b"),
     "We don't do payment plans, so the deposit is fifty percent to start, and the rest when it's done. "
     "I can note your question for Alex if you'd like."),
    (re.compile(r"\b(parent|parents|client|member|customer|student|patient)s? (login|log in|portal|account)\b|\blog ?in\b"),
     "Yes, Alex can build a portal, where people log in to see their own information privately. "
     "He can go over how it would work for you on a free call."),
    (re.compile(r"\b(recordings?|records?|data|information|photos?)\b.*\b(stored|kept|saved|access|private|privacy|confidential|safe)\b|"
                r"\b(confidential\w*|privacy|private)\b"),
     "Good question. I run on a small computer in the office, not in the cloud, so calls stay local and private. "
     "I'm not certified for things like HIPAA, so Alex can go over exactly what's protected on a free call."),
]
ROUND7 = [
    (re.compile(r"\b(five|5|six|6|four|4) (in the morning|a\.? ?m)|\b(24 ?/ ?7|twenty four seven|middle of the night|after hours|"
                r"weekends?|nights?|at night|overnight|all hours)\b"),
     "Yes, a receptionist like me answers any time, day or night, weekends too, so you never miss a call."),
    (re.compile(r"\b(portal|upload\w*|documents?|tax docs|secure\w*|ssn|social security|encrypt\w*|client login)\b"),
     "Yes, Alex builds client portals, where clients log in, upload documents, and check their status. "
     "He builds them with security in mind, and he can go over exactly how your clients' data is protected on a free call."),
    (re.compile(r"\b(donat\w*|tith\w*|offering|give online|livestream\w*|live ?stream\w*|stream (our|the) service)\b"),
     "Yes, Alex can set up online donations and giving on your website, and a live stream of your services. "
     "He can go over how it would work for your church on a free call."),
    (re.compile(r"\b(nonprofit|non-profit|church(es)?|ministr\w*|charit\w*)\b.*\b(discount|price|rate|deal|cheaper)\b|"
                r"\b(discount|price|rate|deal)\b.*\b(nonprofit|non-profit|church(es)?|ministr\w*|charit\w*)\b"),
     "I'm not sure about a nonprofit discount myself, but Alex works with churches and nonprofits, and he'll talk pricing "
     "with you honestly on a free call, with no commitment."),
    (re.compile(r"\b(scam\w*|ripped (me )?off|took my money|disappeared|burned before|got burned|trust (you|a deposit|the deposit)|"
                r"why should i trust)\b"),
     "I'm really sorry that happened to you, and it makes sense to be careful. The first call with Alex is free, with no commitment, "
     "so you can talk to him, ask anything, and decide for yourself before paying anything."),
    (re.compile(r"\b(pray|prayer|prayers|bless me)\b"),
     "That's very kind of you to ask. I'll pass your message along to Alex, and I hope you have a blessed day."),
]
PITCH_SPEECH = ("You're actually talking to one right now! A receptionist like me can answer your calls any time, "
                "answer questions about your business, book appointments, take messages, and hang up on spam calls. "
                "Any business can get one, and the price is flexible based on your size. "
                "Would you like a free call with Alex to set one up?")
LOST_SPEECH = "I'm sorry, I didn't quite follow all of that. What would you like help with today?"
# Never "outside what I can help with": an honest "I don't want to guess" plus a real next step.
UNKNOWN_SPEECH = ("That's a good question, and I don't want to guess. I can book you a free call with Alex "
                  "to go over it, or take a message for him. Which would you prefer?")
NOT_FOLLOW_SPEECH = ("Sorry, I'm not sure I follow. I can tell you about LexMakesIt's websites and prices, "
                     "or book you a free call with Alex. What would help?")
QUESTION = re.compile(r"^(so |and |but |wait |also |hey |um |uh )*(can|could|do|does|did|what|what's|how|is|are|will|would|"
                      r"where|who|why|when|which|any|have)\b|\?\s*$")
# Six simulated callers, 2026-11-26 (tests/test_kindness.py).
MESSAGE = re.compile(r"\b(take|leave|pass( on| along)?) (a |him a |alex a )?message\b|\b(have|ask|get) (him|alex|someone|somebody) (to )?call\b"
                     r"|\b(call|get back to) me( back)?\b|\bcall ?back\b|\bcallback\b")
WAIT = re.compile(r"^(hold on|hang on|one (sec|second|moment|minute)|give me (a|one) (sec|second|minute|moment)|"
                  r"just a (sec|second|moment|minute)|wait a (sec|second|minute))\b")
REAL_EMERGENCY = re.compile(r"\b(fire|ambulance|bleeding|hurt|injured|heart attack|can'?t breathe|police|overdose|gas leak|unconscious)\b")
SITE_TROUBLE = re.compile(r"\b(site|website|page|email)\b.*\b((is|'s|went|has been|keeps|got|been)( going)? (down|broken|offline|hacked)|crashed|(isn'?t|is not|not|stopped|quit) working)\b")
HYPOTHETICAL = re.compile(r"\b(what if|if|in case|whenever|ever|would|could|what happens)\b")
NO_BOOK = re.compile(r"\b(don'?t|do not|not) (want|need|ready) to (book|schedule)|\bnot (ready|yet)\b.*\b(book|schedul)|"
                     r"\bjust (want|wanted|need|needed) (to know|the price|a price|some info|information)|\bnot booking\b")
READBACK = re.compile(r"\b(read|say|repeat|give)\b.*\b(number|phone|name)\b.*\bback\b|\bread (it|that|that number|my number) back\b|"
                      r"\bwhat (number|name) did you (get|put|write|take)\b|\b(what'?s|what is|what was) (my|the) (booking )?code\b|"
                      r"\b(repeat|say|give me) (the|my) (booking )?code\b|\bcode again\b")
PRICE_SHAPE = re.compile(r"\b(onetime|one[- ]?time|once|one off|monthly|every month|per month|a month|each month|up ?front)\b.*\b(or|then|and|plus)\b|\b(is it|is that|is the)\b.*\b(onetime|monthly|every month)\b")
PRICE_SHAPE_SPEECH = ("The website itself is a one-time cost, starting at one thousand dollars. Then it's ten dollars a month "
                      "to keep it managed and running. Alex can give you an exact quote on a free call.")
MONTHLY_Q = re.compile(r"\b(ten|10)( dollars| bucks)?( a| per)? month\b.*\b(cover|for|include|get)\b|\bwhat('?s| is| does) the (monthly|ten dollars?)\b")
MONTHLY_SPEECH = "The ten dollars a month covers managing your website after it's built. Alex can go over the details on a free call."
AI_Q = re.compile(r"\bwhat('?s| is| does)( an?)? (ai|a i|artificial intelligence)( mean)?\b")
AI_SPEECH = ("It just means I'm a computer program that can talk with you, a bit like a very helpful answering machine. "
             "Alex set me up so you can get answers any time. What can I help you with?")
SAID_NAME = re.compile(r"\b(?:my name is|my name'?s|name'?s|this is|it'?s|i'?m|i am|call me)\s+(.*)", re.I)
ORDINAL_PICK = r"(^(the |um |uh |let'?s do |i'?ll take |how about |ok(ay)? )*(the )?{w}( one| slot| option| time)?$|\b{w} (one|slot|option)\b|\bthe {w}\b(?! (thing|of|time i))|\b{w} works\b)"
DISTRESS = re.compile(r"\b(drowning|overwhelmed|stressed|stressful|struggling|burned|burnt|ripped off|scammed|vanished|ghosted|"
                      r"took my money|lost (my |all my )?money|sales are (way |really )?down|can'?t afford|frustrat\w*|upset|worried|"
                      r"scared|nervous|don'?t know where to start|hard time|tough time|rough time|losing customers)\b")
BURNED = re.compile(r"\b(burned|burnt|ripped off|scammed|vanished|ghosted|took my money|took \w+ (thousand|hundred) dollars)\b")
MONEY_WORRY = re.compile(r"\b(can'?t afford|money('?s| is) tight|on a budget|lost (my |all my )?money|fixed income|"
                         r"tight budget|not (a lot of|much) money|social security check|small budget)\b")
LEGIT = re.compile(r"\b(legit|legitimate|a scam|scam|references|trust you)\b")
# "Can I see examples?" gets the owner's private-work answer; "are you legit?" gets the no-commitment call.
PORTFOLIO_Q = re.compile(r"\b(examples?|portfolio|past work|sites you'?ve (made|built|done)|see (your|some) (work|sites))\b")
PORTFOLIO_SPEECH = ("We keep client work private, so we don't share a portfolio. The best way to see what Alex can do "
                    "is a free call, with no commitment, where he talks through your idea and how he'd build it.")
LEGIT_SPEECH = ("Fair question. The best way to see what Alex can do is a free call, with no commitment, "
                "where he talks through your idea and exactly how he'd build it, before you pay anything.")
SPANISH_WORDS = {"hola", "bueno", "mira", "necesito", "quiero", "página", "pagina", "por", "favor", "gracias", "señora",
                 "habla", "hablar", "español", "espanol", "usted", "tengo", "negocio", "restaurante", "llamar", "número"}
ES_DIGITS = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
ES_NAME = re.compile(r"\b(?:me llamo|mi nombre es|soy|habla|le habla)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ]+(?:\s+(?!mi\b|y\b|de\b)[A-ZÁÉÍÓÚÑa-záéíóúñ]+)?)", re.I)
ES_BYE = re.compile(r"^(no,? )?(muchas )?gracias\W*$|\bdios\b|\b(adi[oó]s|chao|bye|eso es todo|es todo|nada m[aá]s|"
                    r"hasta luego|cu[ií]d(ate|ese|ense)|dios (le|te|los) bendiga|que le vaya bien|buenas noches)\b")
ES_SPEAKS = re.compile(r"\b(habla|hablan|hablas|contesta\w*|atiende\w*|funciona)\b.*\bespa[nñ]ol\b|\bespa[nñ]ol\b.*\?")
ES_RECEP = re.compile(r"\b(recepcionista|c[oó]mo funciona|contesta (mi|el|los) (n[uú]mero|tel[eé]fono)|contestar (el|mi|los) tel[eé]fono|contesta(r)? (las )?llamadas|asistente)\b")
ES_TOOLS = re.compile(r"\b(square|clover|toast|quickbooks|conecta\w*|integra\w*)\b")
ES_ORDER = re.compile(r"\b(pedidos?|ordenar|[oó]rdenes|delivery|para llevar)\b")
ES_PRICE = re.compile(r"\b(cu[aá]nto|precio|cuesta|cobran|costo)\b")
ES_DEPOSIT = re.compile(r"\b(dep[oó]sit\w*|pagos|plazos|adelanto|anticipo)\b")
ES_BOOK = re.compile(r"\b(cita|reuni[oó]n|agendar|hablar con alex|llamada)\b")
ES_TIME = re.compile(r"\b(cu[aá]nto tiempo|cu[aá]ndo est[aá]|r[aá]pido|demora|tarda\w*|semanas|lista)\b")
ES = {
    "ask_details": "Con gusto. ¿Me dice su nombre y su número de teléfono, por favor?",
    "got_details": "Gracias, {name}. Tengo el {number}. ¿Qué le digo a Alex?",
    "got_number": "Gracias. Tengo el {number}. ¿Y su nombre?",
    "saved": ("Perfecto{name}. Le paso su mensaje a Alex, y él le va a llamar lo antes posible. "
              "¿Le puedo ayudar con algo más?"),
    "price": ("Una página web empieza en mil dólares, y luego son diez dólares al mes para mantenerla. "
              "Alex le puede dar un precio exacto en una llamada gratis."),
    "deposit": "Para empezar se paga un depósito del cincuenta por ciento, y el resto al terminar.",
    "plans": "No tenemos planes de pago. Para empezar se paga un depósito del cincuenta por ciento, y el resto al terminar.",
    "speaks": ("Sí, yo le atiendo en español con mucho gusto, y le paso todo a Alex con los detalles. "
               "Si Alex habla español, eso mejor se lo confirma él. ¿En qué le puedo ayudar?"),
    "recep": ("Una recepcionista como yo contesta sus llamadas, en inglés o en español, toma mensajes y agenda citas. "
              "El precio es flexible, según el tamaño de su negocio. Alex se lo explica en una llamada gratis."),
    "recep_price": ("El precio es flexible, según el tamaño de su negocio y cuántas llamadas recibe. "
                    "Buen servicio, a buen precio. Alex le da un número exacto en una llamada gratis."),
    "tools": "Alex puede conectar la mayoría de los sistemas, como Square, y le confirma el suyo en la llamada gratis.",
    "order": "Sí, Alex puede hacer pedidos en línea, para que sus clientes ordenen y paguen desde su página.",
    "repeat_number": "Disculpe, creo que me faltó un número. ¿Me lo repite despacio, por favor?",
    "use_caller": "No se preocupe, guardo el número desde el que llama, y Alex le llama ahí. ¿Qué le digo a Alex?",
    "got_name": "Mucho gusto, {name}. ¿Y su número de teléfono?",
    "lost2": "No se preocupe. Le dejo un mensaje a Alex con el número desde el que llama. ¿Qué le quiere decir?",
    "time": "Depende del proyecto. Una página sencilla puede estar lista en veinticuatro horas, y una más grande hasta en un mes.",
    "book": "Con gusto. Alex le va a llamar para hablar de su idea. ¿Me dice su nombre y su número?",
    "lost": "Disculpe, no le entendí bien. ¿Me puede decir su nombre y su número, y Alex le llama?",
    "bye": "Gracias por llamar{name}. ¡Que le vaya muy bien!",
}


ES_DIGIT_WORDS = {w: str(i) for i, w in enumerate(ES_DIGITS)} | {"o": "0", "oh": "0"}


def es_digits(text: str) -> str:
    """'nueve siete tres, 5 5 5' -> '973555': Spanish digit words and numerals."""
    return "".join(t if t.isdigit() else ES_DIGIT_WORDS.get(t, "") for t in re.findall(r"[a-záéíóúñ]+|\d", text.lower()))


def spoken_number_es(digits: str) -> str:
    d = digits[1:] if len(digits) == 11 and digits[0] == "1" else digits
    groups = [d[:3], d[3:6], d[6:]] if len(d) == 10 else [d]
    return ", ".join(" ".join(ES_DIGITS[int(c)] for c in g) for g in groups if g)


# What English-only ears make of Spanish ("Ola, Fiono, Mira, Nesocito una pajino"): sound-alikes.
SPANISH_GARBLE = re.compile(r"\b(h?ola|mira|ne[sc]+[eio]s?[aeiou]?[ct]i?to|pa[gjh]in[ao]|[ck]u?[ae]?nto|[ck]ust?a|quier?o|quiro|"
                            r"gra[cs]ias|bueno|se[nñ]or[ae]?|por favor|resta?u?rante|tarante|numero|minumo|"
                            r"llamo|milamo|pagar\w*|dep[oó]sito|bizzito|pedido|padido|linya|l[ií]nea|una|para)\b", re.I)
SPANISH_LEAD = ("Hola, gracias por llamar. Con gusto le ayudo. Alex le puede llamar. "
                "¿Me dice su nombre y su número de teléfono, por favor?")
SPANISH = re.compile(r"\b(spanish|espa[nñ]ol|habla)\b")
SPANISH_SPEECH = ("I'm sorry, I can only speak English right now. I'll keep it simple, "
                  "and I can take a message for Alex if that's easier.")
WEATHER = re.compile(r"\b(cold|hot|freezing|rain\w*|snow\w*|weather|humid|chilly)\b.*\b(today|out|outside|isn'?t it|huh)\b")
REPEAT_ASK = re.compile(r"\b(slower|more slowly|slow down|what was that|say that again|repeat that|come again|pardon|one more time|again,? slowly|"
                        r"didn'?t catch that|what did you say|could you repeat)\b")
# "Did you cancel it?" / "is it still booked?" is a question, never a cancel.
STATUS_Q = re.compile(r"\b(did you|was it|is it|has it been|have you) (cancel|cancelled|canceled)\b|\bstill (there|booked|on|good|scheduled|happening)\b")
URGENT = re.compile(r"\burgent\b|\basap\b|\bas soon as possible\b|\bright away\b|\btoday\b.*\bcall\b|\bcall\b.*\btoday\b")
WHEN_CALL = re.compile(r"\b(will|when will|is|when is|can|could) (he|alex) (call|get back|be calling|reach)\b|\bpromise\b")
MOVE = re.compile(r"\b(move|reschedule|change|switch|push)\b.*\b(it|that|appointment|booking|call)\b|\binstead\b")
CANCEL_IT = re.compile(r"\bcancel (it|that|this|my (appointment|booking|call)|the (appointment|booking|call))\b|^cancel$")
NAME_LEAD = re.compile(r"^((hi|hey|yeah|yes|oh|sure|okay|ok|well|um|uh|so)\W+)*((tell|let) (him|alex)( know)?( that)?( it'?s| this is)?\s+)?"
                       r"(it'?s|this is|my name is|name'?s|i'?m|i am|call me)?\s*", re.I)
DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
TALK_TO_SPEECH = ("Alex isn't available right this second, but I can take a message and have him call you back, "
                  "or book you a free call with him. Which would you prefer?")
NOT_A_NAME = {"mornings", "morning", "afternoons", "afternoon", "evenings", "evening", "nights", "weekdays", "weekends",
              "after", "before", "anytime", "today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday", "best", "fine", "alright", "good", "great", "perdón", "perdon", "disculpe", "oiga", "mire", "bueno", "pues",
              "my", "tell", "him", "her", "let", "from", "ask", "have", "please", "number", "phone", "blah", "you", "nobody", "whatever", "someone", "somebody", "anybody", "anyone", "me", "test",
              "yes", "yeah", "no", "nope", "sure", "okay", "ok", "hello", "hi", "um", "uh", "the", "a", "actually", "wait",
              "si", "sí", "bueno", "mira", "hola", "for", "about", "our", "we", "he", "she", "they", "calling", "here", "speaking",
              "can", "could", "would", "will", "do", "does", "did", "how", "what", "when", "where", "why", "who", "is", "are",
              "cancel", "move", "book", "thanks", "thank", "never", "i", "it", "that", "this", "just", "hold", "hang",
              "hmm", "and", "but", "not", "sorry", "please", "great", "perfect", "alright", "cool", "good"}


def spoken_number(digits: str) -> str:
    """'9085501422' -> 'nine oh eight, five five oh, one four two two' (how people read numbers back)."""
    w = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    d = digits[1:] if len(digits) == 11 and digits[0] == "1" else digits
    groups = [d[:3], d[3:6], d[6:]] if len(d) == 10 else [d]
    return ", ".join(" ".join(w[int(c)] for c in g) for g in groups if g)


def spoken_digits(text: str) -> str:
    return "".join(t if t.isdigit() else DIGIT_WORDS.get(t, "") for t in re.findall(r"[a-z]+|\d", text.lower()))


DAYTIME = (r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|morning|afternoon|evening|"
           r"\d{1,2}(?::\d\d)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon)"
           r"(?:\s*(?:o'?\s?clock|a\.? ?m\.?|p\.? ?m\.?|in the (?:morning|afternoon|evening)))?")
_SELF_FIX = re.compile(rf"\b{DAYTIME}\W+(?:(?:no|nope|wait|sorry|actually|i mean|scratch that)\W+)+(?={DAYTIME}\b)", re.I)
_NOT_THAT = re.compile(rf"\bnot (?:at |on )?{DAYTIME}\W*", re.I)


NUM_RUN = re.compile(r"(?:\b(?:\d+|zero|oh|one|two|three|four|five|six|seven|eight|nine)\b[\s,.-]*){2,}", re.I)


def phone_in(text: str) -> tuple[str | None, str]:
    """('8625550119', rest of the words) when a whole phone number was said, else (None, text)."""
    for m in NUM_RUN.finditer(text):
        d = spoken_digits(m.group(0))
        if len(d) == 10 or (len(d) == 11 and d[0] == "1"):
            return d, (text[:m.start()] + " , " + text[m.end():])
    return None, text


def collapse_repeats(text: str) -> str:
    """Phone ASR sometimes loops a sentence ('How much? How much? How much?'): keep one."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = []
    for x in parts:
        if not out or x.strip().lower() != out[-1].strip().lower():
            out.append(x)
    if len(out) == 1 and len(parts) > 1:
        return out[0]
    words = text.split()
    for n in range(3, len(words) // 2 + 1):           # a run of words repeated back to back
        if words[:n] * (len(words) // n) == words[:n * (len(words) // n)] and len(words) // n >= 2:
            return " ".join(words[:n])
    return " ".join(out)


BOOKING_STEPS = {"offer_slots", "ask_which", "ask_day", "confirm_required", "ask_again_time", "ask_again_day",
                 "slot_taken", "closed_day", "ask_time"}


def corrected(text: str) -> str:
    """'Tuesday, no, no, Wednesday' -> 'Wednesday'; 'not ten, two o'clock' -> 'two o'clock'."""
    return _NOT_THAT.sub("", _SELF_FIX.sub("", text)).strip()
# Thinking noises and speech-to-text noise tags: a person waits, they don't answer them.
FILLER = re.compile(r"^\W*((um+|uh+|hmm+|so|you know|whoa|like|well|[\[(][^\])]*[\])])\W*)+$")
# A leading "okay, like ..." / "so ..." is a discourse marker, not an answer.
LEAD_IN = re.compile(r"^(okay|ok|alright|so|well|um|uh)\b[\s,]*(so\s+|like\s+|um\s+|uh\s+)*")
# A vague period ("next week") still needs a day; otherwise offer the soonest times.
VAGUE_PERIOD = re.compile(r"\b(next week|this week|sometime|some time|later|weekend)\b")
REPHRASE = {"ask_again_time": "Sorry, I missed the time. You can say something like ten a m.",
            "ask_again_day": "Sorry, I didn't catch the day. Something like tomorrow, or Thursday?",
            "confirm_again": "Sorry, I just need a yes or a no. Should I book it?",
            "repeat": "Sorry, it's a little hard to hear. Could you say that one more time?",
            "presence": "Yep, still here. What can I help you with?"}
PRESENCE = re.compile(r"\b(are you (still )?there|can you hear me|you still there|is anyone there)\b")
PRESENCE_REPLY = "Yes, I'm here! How can I help?"
OWN_BUSINESS = re.compile(r"\bi (have|own|run|operate|started|just opened|opened) (a|an|my)\b.*\b(business|shop|store|salon|"
                          r"restaurant|bakery|barbershop|company|place|cafe|gym|studio|agency|clinic|practice|truck)\b")
PITCH = ("Nice! For a business like yours, we can set up a website, online booking, and automations like "
         "missed call text back. Want to book a free call with Alex to talk it over?")
FILLER_REPLIES = ("Take your time.", "Mm-hm.")
CONFIRM_TAIL = re.compile(r"\b(yes|yeah|yep|yes please|please book it|book it|that works|sounds? good)\s*(please|thank you|thanks)?\s*$")
LOCATION = re.compile(r"\b(where|address|located|location|directions|parking)\b")
OPEN_DAY = re.compile(r"\b(are you|you guys|is it|is the office)\s+open\b|\bopen on\b")


@dataclass
class When:
    date: date | None = None
    time: time | None = None
    part: str | None = None


def split_first(text: str) -> str:
    """The first sentence of a reply."""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


def parse_when(phrase: str, now: datetime) -> When:
    t = dr._normalise(phrase)
    t = re.sub(r"\bo ?clock\b", "", t)
    w = When()
    d, roll = dr._date(t, now.date())
    if d is not None:
        if roll and d < now.date():
            d = d.replace(year=d.year + 1)
        w.date = d
    # Remove the date words so a day-of-month isn't read as an hour.
    rest = re.sub(r"\b(" + "|".join(dr.MONTHS) + r")\s+\d{1,2}\b|\b\d{1,2}\s+of\s+(" + "|".join(dr.MONTHS) + r")\b", " ", t)
    rest = re.sub(r"\b20\d\d\b", " ", rest)
    m = re.search(r"\b(\d{1,2})(?::(\d{2})|\s(\d{2})(?!\d))?\s*(am|pm|in the morning|in the afternoon|in the evening|at night)?(?=\b)", rest)
    if m:
        hour, minute, suffix = int(m.group(1)), int(m.group(2) or m.group(3) or 0), m.group(4)
        if minute < 60:
            if suffix in ("pm", "in the afternoon", "in the evening", "at night") and 1 <= hour <= 11:
                hour += 12
            elif suffix in ("am", "in the morning") and hour == 12:
                hour = 0
            elif suffix is None and 1 <= hour <= 7:
                hour += 12                                  # business hours: "at two" means 2 pm
            if 0 <= hour <= 23:
                w.time = time(hour, minute)
    if re.search(r"\bnoon|midday\b", t):
        w.time = time(12)
    for part in PARTS:
        if re.search(rf"\b{part}\b", t) and not (m and m.group(4) and part in m.group(4)):
            w.part = part
    return w


@dataclass
class _Call:
    date: date | None = None
    time: time | None = None
    part: str | None = None
    offered: list[datetime] = field(default_factory=list)


class Brain:
    def __init__(self, store: BookingStore, intents, timezone_name: str = "America/New_York",
                 hours=None, profile: dict | None = None, require_caller_id: bool = False, clock=None, knowledge=None,
                 seed: int | None = None, on_message=None):
        self.store, self.intents = store, intents
        self.tz = ZoneInfo(timezone_name)
        self.profile = profile or {}
        self.hours = hours if hours is not None else (self.profile.get('hours_parsed') or DEFAULT_HOURS)
        self.clock = clock or (lambda: datetime.now(self.tz))
        self._last: dict[str, str] = {}
        self._last_speech: dict[str, str | None] = {}
        self._fillers: dict[str, int] = {}
        self.knowledge = knowledge
        self.on_message = on_message              # called with {name, callback}, never the words
        self.dialogue = Dialogue(store, timezone_name=timezone_name, hours=self.hours,
                                 require_caller_id=require_caller_id, clock=self.clock)
        self._calls: dict[str, _Call] = {}
        self._msg: dict[str, dict] = {}           # message being taken: step, name, callback, body
        self._booked: dict[str, str] = {}         # this call's booking reference (move/cancel "it")
        self._names: dict[str, str] = {}          # the caller's name, once given
        self._felt: dict[str, int] = {}           # times we've acknowledged feelings this call
        self._moving: dict[str, bool] = {}        # caller asked to move this call's booking
        self._utter: dict[str, str] = {}
        self._callbacks: dict[str, str] = {}
        self._nowhere: dict[str, int] = {}
        self._lang: dict[str, str] = {}            # "es" once the caller speaks Spanish
        self._es: dict[str, dict] = {}             # the Spanish call's message in progress
        self._topic: dict[str, str] = {}           # what the call is about ("receptionist" after the pitch)
        self._real: dict[str, bool] = {}           # the caller has said something real this call         # turns in a row that went nowhere
        self._given: dict[str, dict[str, str]] = {}   # answer text -> the question it first answered
        self._last_msg: dict[str, int] = {}      # number given for a callback (read back on request)          # the caller's previous words (a repeat vs a new question)
        import random as _random
        self._rng = _random.Random(seed)

    # ---- answers -------------------------------------------------------------------
    def _hours_speech(self) -> str:
        days = sorted(self.hours)
        if not days:
            return "We're currently closed."
        names = [d.capitalize() for d in DAY_WORDS]
        span = f"{names[days[0]]} to {names[days[-1]]}" if days == list(range(days[0], days[-1] + 1)) else \
            ", ".join(names[d] for d in days)
        o, c = self.hours[days[0]]
        fmt = lambda t: spoken_time(datetime.combine(date.today(), t))
        return f"We're open {span}, {fmt(o)} to {fmt(c)}."

    def _open_on(self, day: date) -> Reply:
        name = DAY_WORDS[day.weekday()].capitalize()
        span = self.hours.get(day.weekday())
        if not span:
            speech = f"We're closed on {name}s. {self._hours_speech()}"
        else:
            fmt = lambda t: spoken_time(datetime.combine(day, t))
            speech = f"Yes, on {name}s we're open {fmt(span[0])} to {fmt(span[1])}."
        return Reply(speech, "faq", speech=f"Sure. {speech}")

    def _faq(self, text: str) -> Reply:
        topic, ans = profile_answer(self.profile, text)
        if ans:
            return Reply(ans, "faq", speech=f"Sure. {ans}")
        if topic or not re.search(r"\b(hours|open|close|closing|opening)\b", text.lower()):
            return self._unknown()
        speech = self._hours_speech()
        return Reply(speech, "faq", speech=f"Sure. {speech}")

    @staticmethod
    def _unknown() -> Reply:
        return Reply("That's outside what I can help with, but I can answer questions about LexMakesIt or book you a free call with Alex.", "unknown",
                     speech=UNKNOWN_SPEECH)

    # ---- calendar ------------------------------------------------------------------
    def _free_slots(self, day: date, part: str | None, n: int = 3) -> list[datetime]:
        span = self.hours.get(day.weekday())
        if not span:
            return []
        taken = [(datetime.fromisoformat(r["starts"]), datetime.fromisoformat(r["ends"])) for r in self.store.active()]
        now = self.clock()
        free = []
        for h in PREFERRED_HOURS:
            start = datetime.combine(day, time(h), self.tz)
            end = start + timedelta(minutes=30)
            if not (span[0] <= start.time() and end.time() <= span[1]) or start <= now:
                continue
            if part and not (PARTS[part][0] <= start.time() < PARTS[part][1]):
                continue
            su, eu = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
            if any(a < eu and b > su for a, b in taken):
                continue
            free.append(start)
            if len(free) == n:
                break
        return sorted(free)

    def _open_at(self, day: date, at: time) -> bool:
        span = self.hours.get(day.weekday())
        end = (datetime.combine(day, at) + timedelta(minutes=30)).time()
        return bool(span) and span[0] <= at and end <= span[1] and end > at

    def _next_open_day(self, day: date) -> date | None:
        for i in range(1, 8):
            d = day + timedelta(days=i)
            if self.hours.get(d.weekday()):
                return d
        return None

    @staticmethod
    def _list(slots: list[datetime]) -> str:
        times = [spoken_time(s) for s in slots]
        return times[0] if len(times) == 1 else ", ".join(times[:-1]) + f", or {times[-1]}"

    @staticmethod
    def _day_words(day: date, today: date) -> str:
        if day == today:
            return "today"
        if day == today + timedelta(days=1):
            return "tomorrow"
        return DAY_WORDS[day.weekday()].capitalize()

    # ---- the conversation ----------------------------------------------------------
    def handle(self, utterance: str, session_id: str, caller_id: str | None = None) -> Reply:
        s, low = session_id, " ".join(re.sub(r"[.!?,]+", " ", utterance.lower()).split())
        if ROBOCALL.search(utterance):
            # A recorded sales script: end it before it burns minutes or lands on the calendar.
            self._msg.pop(s, None)
            self.dialogue._pending.pop(s, None)
            return Reply("Goodbye.", "hangup", speech=SPAM_SPEECH)
        if self._lang.get(s) == "es":
            # Spanish calls answer in Spanish; the English rephrasing below doesn't apply.
            return self._handle(utterance, session_id, caller_id)
        utterance = collapse_repeats(utterance)
        low = " ".join(re.sub(r"[.!?,]+", " ", utterance.lower()).split())
        in_booking = self._last.get(s) in BOOKING_STEPS or s in self.dialogue._pending or bool(self._calls.get(s) and self._calls[s].offered)
        r = self._handle(utterance, session_id, caller_id)
        offered_call = any(w in (self._last_speech.get(s) or "") for w in ("free call", "book")) \
            and bool(AFFIRM.match(low) or re.search(r"\b(let'?s|go over|sounds good|sure)\b", low))
        if r.action in ("offer_slots", "confirm_required", "closed_day") and not in_booking and not offered_call \
                and not WANTS_BOOKING.search(low):
            # They didn't ask to book ("what's 2+2?", "client portal charges?"): answer, don't schedule.
            hit = self.knowledge.best(utterance) if self.knowledge is not None else None
            timed = any(vars(parse_when(utterance, self.clock())).values()) and not re.search(r"\b(plus|minus|times|divided)\b", low)
            if hit:
                r = Reply(hit[1], "knowledge", speech=hit[1])
            elif not timed:
                r = Reply(UNKNOWN_SPEECH, "unknown", speech=UNKNOWN_SPEECH)
        if r.action in ("offer_slots", "confirm_required") and re.match(r"^(is|does|do|can|could|will|would) (it|this|that|you|your|she)\b", low) \
                and not BOOK_WORDS.search(low) and not any(vars(parse_when(utterance, self.clock())).values()):
            # "Is it HIPAA compliant?" isn't a booking request.
            hit = self.knowledge.best(utterance) if self.knowledge is not None else None
            r = Reply(hit[1], "knowledge", speech=hit[1]) if hit else Reply(UNKNOWN_SPEECH, "unknown", speech=UNKNOWN_SPEECH)
        n = self._nowhere.get(s, 0) + 1 if r.action in UNPRODUCTIVE else (self._nowhere.get(s, 0) if r.action == "filler" else 0)
        self._nowhere[s] = n
        if r.action not in UNPRODUCTIVE and r.action != "filler":
            self._real[s] = True
        if n >= 3 and not self._booked.get(s) and not self._msg.get(s) and not self._real.get(s):
            # Three turns in a row going nowhere (nonsense, silence, a broken line): let them go kindly.
            return Reply("Goodbye.", "hangup", speech=NOWHERE_SPEECH)
        # Mid-booking, a SHORT reply we can't place is almost always a misheard answer ("Ten." -> "Pen"):
        # a person asks again. A real question ("do you do payment plans?") gets a real answer.
        last = self._last.get(session_id)
        short = len(low.split()) <= 3
        if r.action == "unknown" and short and last in ("offer_slots", "ask_which"):
            r = Reply("Sorry, which time was that?", "ask_again", speech="Sorry, which time was that?")
            last = "ask_again_time"
        elif r.action == "unknown" and short and last == "ask_day":
            r = Reply("Sorry, which day was that?", "ask_again", speech="Sorry, which day was that?")
            last = "ask_again_day"
        else:
            if r.action in ("unknown", "faq") and self.knowledge is not None:
                hit = self.knowledge.best(" ".join(utterance.split()))
                # A keyword answer ("website" -> contact) loses to a close checked answer; an unknown
                # takes a fair match only ("see examples of your work" is not the reviews answer).
                if hit and hit[2] >= (UNKNOWN_MATCH if r.action == "unknown" else STRONG_MATCH):
                    r = Reply(hit[1], "knowledge", speech=hit[1])
            if r.action == "unknown" and len(utterance.split()) > 15:
                r = Reply(LOST_SPEECH, "lost", speech=LOST_SPEECH)
            elif r.action == "unknown" and not QUESTION.search(low):
                # Not a question (side talk, a garbled line): don't send it to Alex.
                r = Reply(NOT_FOLLOW_SPEECH, "unknown", speech=NOT_FOLLOW_SPEECH)
            last = r.action
        self._last[session_id] = last
        if r.action in ("booked", "rescheduled") and r.committed_id:
            self._booked[s] = r.committed_id
            self._moving.pop(s, None)
            if r.action == "booked" and closing(low):
                # "Okay perfect, thanks, bye": confirm it and let them go.
                body = (r.speech or r.text).replace(ANYTHING_ELSE, "").strip()
                r = Reply(r.text, r.action, r.committed_id, speech=f"{body} Thanks for calling, and take care!")
            elif r.action == "booked" and s not in self._names:
                # Alex needs to know who he's calling (every simulated caller noticed it never asked).
                body = (r.speech or r.text).replace(ANYTHING_ELSE, "").strip()
                self._msg[s] = {"step": "booking_name", "ref": r.committed_id}
                r = Reply(r.text, r.action, r.committed_id,
                          speech=f"{body} Can I get your name, so Alex knows who he's talking to?")
        if r.action in ("faq", "knowledge") and not in_booking:
            r = self._both(utterance, r, s, caller_id)
        r = self._vary(r)
        if DISTRESS.search(low) and r.action != "goodbye":
            r = self._acknowledge(r, low, s)
        given = self._given.setdefault(s, {})
        if r.action in ("faq", "knowledge", "pitch") and r.text in given and given[r.text] != low \
                and r.speech != self._last_speech.get(session_id):
            # The same blurb earlier in the call, for a different question: it didn't fit then either.
            r = Reply(UNKNOWN_SPEECH, "unknown", speech=UNKNOWN_SPEECH)
        elif r.action in ("faq", "knowledge", "pitch"):
            given.setdefault(r.text, low)
        if r.speech and r.speech == self._last_speech.get(session_id):
            # People rephrase when they repeat themselves; identical twice sounds like a recording.
            again = REPHRASE.get(last) or REPHRASE.get(r.action)
            if again is None and r.action == "unknown":
                again = ("I'm not sure on that one either, I'm sorry. Alex would know, though. "
                         "Should I take a message for him, or book you a free call?")
            elif again is None and low != self._utter.get(s) and r.action in ("faq", "knowledge", "pitch"):
                # A different question that lands on the same answer: that answer doesn't fit.
                r, again = Reply(UNKNOWN_SPEECH, "unknown", speech=UNKNOWN_SPEECH), UNKNOWN_SPEECH
            elif again is None:
                body = r.speech
                first = split_first(body)
                if first in OPENERS and first != body:
                    body = body[len(first):].strip()
                again = f"Sure, once more. {body}"
            r = Reply(r.text, r.action, r.committed_id, speech=again)
        self._last_speech[session_id] = r.speech
        self._utter[s] = low
        return r

    def _acknowledge(self, r: Reply, low: str, s: str) -> Reply:
        """Feelings first: 'I'm drowning' got 'Nice! For a business like yours...'."""
        n = self._felt.get(s, 0)
        self._felt[s] = n + 1
        if n:
            ack = "I hear you."
        elif BURNED.search(low):
            ack = "Oh no, I'm really sorry that happened to you. That's awful, and it makes sense to be careful."
        elif MONEY_WORRY.search(low):
            ack = "I completely understand. Money is tight for a lot of small businesses right now."
        else:
            ack = "I'm really sorry, that sounds really stressful."
        body = r.speech or r.text
        if r.action in ("unknown", "lost", "pitch"):
            body = ("Alex helps small businesses with exactly this, and the first call is free. "
                    "I can book you one, or take a message so he can call you back. Which would be better?")
        first = split_first(body)
        if first in OPENERS and first != body:
            body = body[len(first):].strip()
        return Reply(r.text, r.action, r.committed_id, speech=f"{ack} {body}")

    # ---- messages --------------------------------------------------------------------
    @staticmethod
    def _name_from(text: str) -> str | None:
        m = SAID_NAME.search(text)
        if not m and len(text.split()) > 6:
            return None                       # names come short; a long answer is something else
        src = m.group(1) if m else NAME_LEAD.sub("", text.strip())
        first = (src.split() or [""])[0].strip(",.")
        if m and (first.lower() in NOT_A_NAME or not first[:1].isupper()):
            # "Dorothy, I'm calling about..." / "I'm panicking": the name isn't after "I'm".
            src = text[:m.start()]
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]*", src)
        dedup = [w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower()]
        if not dedup or dedup[0].lower() in NOT_A_NAME:
            return None
        name = [dedup[0]]
        if len(dedup) > 1 and len(dedup) <= 3 and dedup[1][0].isupper() and not dedup[1].isupper() \
                and "'" not in dedup[1] and dedup[1].lower() not in NOT_A_NAME | {"my", "i", "and", "i'm"}:
            name.append(dedup[1])
        return " ".join(w[0].upper() + w[1:] for w in name)

    def _both(self, utterance: str, r: Reply, s: str, caller_id: str | None) -> Reply:
        """'How much is it and how long does it take?': answer each question, not just one."""
        parts = [x.strip() for x in re.split(r"\band (how|what|do|does|can|is|who|when|where)\b", utterance, flags=re.I) if x and x.strip()]
        qs, i = [], 0
        while i < len(parts):
            word = parts[i]
            if word.lower() in ("how", "what", "do", "does", "can", "is", "who", "when", "where") and i + 1 < len(parts):
                word = word + " " + parts[i + 1]
                i += 1
            if len(word.split()) >= 3:
                qs.append(word)
            i += 1
        if len(qs) < 2:
            return r
        said = [r.speech or r.text]
        for q in qs:
            other = self._handle(q, s + "#both", caller_id)
            self._calls.pop(s + "#both", None)
            text = other.speech or other.text
            if other.action in ("faq", "knowledge") and text and all(text[:40] not in x for x in said):
                said.append(text)
        if len(said) == 1:
            return r
        words = " ".join(said)
        return Reply(words, r.action, speech=words)

    def _spanish(self, text: str, low: str, s: str, caller_id: str | None) -> Reply:
        """A Spanish call: answers, name and number in any order, the message, goodbye. Never English,
        never the same line twice in a row."""
        m = self._es[s]
        num, rest = phone_in(text)
        d_es = es_digits(text)
        if not num and (len(d_es) == 10 or (len(d_es) == 11 and d_es[0] == "1")):
            num = d_es
        got = ES_NAME.search(text)
        name = got.group(1).strip() if got else None
        if name and name[:1].islower() and len(name.split()) == 1:
            name = None                               # "soy jardinero": a trade, not a name
        if name and name.split()[0].lower() in NOT_A_NAME:
            name = None
        if not name and not num and m.get("want") in ("name", "details"):
            name = self._lead_name(text)                # "Carmen Rosario, Panadería La Dominicana"
            if name and name.split()[0].lower() in NOT_A_NAME:
                name = None
        if name:
            name = " ".join(w[0].upper() + w[1:] for w in name.split())
        digits = num or (d_es if len(d_es) > len(spoken_digits(text)) else spoken_digits(text))

        def say(key, action, **kw):
            words = ES[key].format(**kw)
            if words == m.get("last"):
                key = "lost2" if key in ("lost", "ask_details") else key
                words = ES[key].format(**kw)
            m["last"] = words
            return Reply(words, action, speech=words)

        def save():
            body = m.get("body") or ("Llamada en español: " + m.get("first", ""))
            self._last_msg[s] = self.store.add_message(m.get("name"), m.get("callback") or caller_id,
                                                       "[ES] " + body, caller_id=caller_id)
            if self.on_message:
                try:
                    self.on_message({"name": m.get("name"), "callback": m.get("callback") or caller_id,
                                     "urgent": bool(re.search(r"\burgent|emergencia|urgente|ya mismo\b", body.lower())),
                                     "language": "es"})
                except Exception:
                    pass
            m["saved"] = True

        def first(n):
            return n.split()[0]

        if ES_BYE.search(low) and len(low.split()) <= 8:
            if (m.get("callback") or m.get("name") or m.get("body")) and not m.get("saved"):
                save()
            n = f", {first(m['name'])}" if m.get("name") else ""
            return say("bye", "goodbye", name=n)
        if name:
            m["name"] = name
        if num:
            m["callback"] = num
        elif len(digits) >= 5 and m.get("want") in ("number", "details", None):
            m["tries"] = m.get("tries", 0) + 1
            if m["tries"] >= 2 and caller_id:
                m["callback"] = caller_id
                m["want"] = "body"
                return say("use_caller", "ask_message")
            m["want"] = "number"
            return say("repeat_number", "ask_number")
        if num or name:
            if m.get("name") and m.get("callback"):
                m["want"] = "body"
                return say("got_details", "ask_message", name=first(m["name"]), number=spoken_number_es(m["callback"]))
            if m.get("callback"):
                m["want"] = "name"
                return say("got_number", "ask_name", number=spoken_number_es(m["callback"]))
            m["want"] = "number"
            return say("got_name", "ask_number", name=first(m["name"]))
        if m.get("want") == "body" and len(low.split()) >= 3 and not re.search(r"[?¿]", text):
            m["body"] = text.strip()
            save()
            m["want"] = None
            n = f", {first(m['name'])}" if m.get("name") else ""
            return say("saved", "message_taken", name=n)
        if ES_SPEAKS.search(low):
            return say("speaks", "knowledge")
        if ES_RECEP.search(low):
            m["topic"] = "recep"
            return say("recep", "pitch")
        if ES_TOOLS.search(low):
            return say("tools", "knowledge")
        if ES_DEPOSIT.search(low):
            return say("plans" if re.search(r"\b(plazos|planes|pagos)\b", low) else "deposit", "knowledge")
        if ES_TIME.search(low):
            return say("time", "knowledge")
        if ES_ORDER.search(low):
            return say("order", "knowledge")
        if ES_PRICE.search(low):
            return say("recep_price" if m.get("topic") == "recep" else "price", "knowledge")
        if m.get("want") == "body" and len(low.split()) >= 3:
            m["body"] = text.strip()
            save()
            m["want"] = None
            n = f", {first(m['name'])}" if m.get("name") else ""
            return say("saved", "message_taken", name=n)
        if ES_BOOK.search(low):
            m["want"] = "details"
            return say("book", "ask_name")
        m["misses"] = m.get("misses", 0) + 1
        if m["misses"] >= 3 and m.get("want") == "body":
            # Still can't make it out: save what we have rather than loop.
            m["body"] = m.get("body") or "No se entendió el mensaje; por favor llamar."
            save()
            m["want"] = None
            n = f", {first(m['name'])}" if m.get("name") else ""
            return say("saved", "message_taken", name=n)
        if m["misses"] >= 2:
            m["callback"] = m.get("callback") or caller_id
            m["want"] = "body"
            return say("lost2", "ask_message")
        m["want"] = "details"
        return say("lost", "unknown")

    def _lead_name(self, rest: str) -> str | None:
        """A name said next to a number: 'Kim, Bella Rose Boutique, <number>' / '<number>, Walter Brennan'."""
        m = re.search(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) from\b", rest)
        if m and m.group(1).split()[0].lower() not in NOT_A_NAME:
            return m.group(1)
        if SAID_NAME.search(rest):
            got = self._name_from(SAID_NAME.search(rest).group(0))
            if got:
                return got
        for chunk in re.split(r"[,.;?!]+", rest):
            w = chunk.split()
            if 1 <= len(w) <= 3 and all(x[:1].isupper() for x in w) and w[0].lower() not in NOT_A_NAME:
                return self._name_from(chunk)
        return None

    def _start_message(self, s: str, caller_id: str | None, lead: str, body: str | None = None) -> Reply:
        flow = self._msg[s] = {"step": "name", "body": body}
        self._calls[s] = _Call()
        if s in self._names:
            flow["name"] = self._names[s]
            return self._ask_number(flow, caller_id, lead)
        return Reply(f"{lead} What's your name?", "ask_name", speech=f"{lead} What's your name?")

    def _ask_number(self, flow: dict, caller_id: str | None, lead: str | None = None) -> Reply:
        flow["step"] = "number"
        head = lead or f"Thanks, {flow['name']}."
        q = "Is the number you're calling from the best one to reach you?" if caller_id else \
            "What's the best number for Alex to call you back on?"
        return Reply(f"{head} {q}", "ask_number", speech=f"{head} {q}")

    def _message_step(self, flow: dict, text: str, low: str, s: str, caller_id: str | None) -> Reply | None:
        step = flow["step"]
        if step == "booking_name":
            self._msg.pop(s, None)
            name = self._name_from(text) if len(low.split()) <= 5 else None
            if not name:
                return None                       # they moved on; handle the turn normally
            self._names[s] = name
            self.store.set_name(flow["ref"], name, caller_id)
            words = f"Thanks, {name}! {ANYTHING_ELSE}"
            return Reply(words, "name_taken", speech=words)
        if step == "name":
            num, rest = phone_in(text)
            if num:
                flow["callback"] = self._callbacks[s] = num
                name = self._lead_name(rest) or self._names.get(s)
                if not name:
                    words = f"Thanks, I've got {spoken_number(num)}. And what's your name?"
                    return Reply(words, "ask_name", speech=words)
                flow["name"] = self._names[s] = name
                said_more = [c.strip() for c in re.split(r"[,.;]+", rest) if len(c.split()) >= 4]
                if said_more and not flow.get("body"):
                    flow["body"] = re.sub(r"^(and |please |can you )*(tell|let) (him|alex) (know )?(that )?", "", said_more[-1], flags=re.I)
                if flow.get("body"):
                    return self._save_message(flow, s, caller_id, spoken_number(num))
                flow["step"] = "body"
                words = f"Got it, {name}. I have {spoken_number(num)}. What would you like me to tell Alex?"
                return Reply(words, "ask_message", speech=words)
            name = self._name_from(text)
            if not name and (QUESTION.search(low) or BOOK_WORDS.search(low)):
                self._msg.pop(s, None)            # they asked something else: answer that instead
                return None
            if not name and flow.get("name_tries", 0) >= 1:
                # Asked twice already: a name isn't worth a loop. Carry on without it.
                flow["name"] = None
                if caller_id and not flow.get("callback"):
                    flow["callback"] = self._callbacks[s] = caller_id
                if flow.get("body"):
                    return self._save_message(flow, s, caller_id)
                flow["step"] = "body"
                words = "No worries. What would you like me to tell Alex?"
                return Reply(words, "ask_message", speech=words)
            if not name:
                flow["name_tries"] = flow.get("name_tries", 0) + 1
                return Reply("Sorry, what was your name?", "ask_name", speech="Sorry, what was your name?")
            flow["name"] = self._names[s] = name
            if flow.get("callback"):
                if flow.get("body"):
                    return self._save_message(flow, s, caller_id, spoken_number(flow["callback"]))
                flow["step"] = "body"
                words = f"Thanks, {name}. What would you like me to tell Alex?"
                return Reply(words, "ask_message", speech=words)
            return self._ask_number(flow, caller_id)
        if step == "number":
            digits = spoken_digits(text)
            heard_back = None
            if len(digits) == 10 or (len(digits) == 11 and digits[0] == "1"):
                flow["callback"] = digits
                heard_back = spoken_number(digits)
            elif len(digits) >= 3 and flow.get("tries", 0) >= 1 and caller_id:
                # Phone-quality speech drops digits; after two tries, don't loop the caller.
                flow["callback"] = caller_id
                flow["step"] = "body"
                self._callbacks[s] = caller_id
                words = ("No worries, I'll save the number you're calling from, and Alex can reach you there. "
                         "What would you like me to tell him?")
                if flow.get("body"):
                    return self._save_message(flow, s, caller_id)
                return Reply(words, "ask_message", speech=words)
            elif len(digits) >= 3:
                flow["tries"] = flow.get("tries", 0) + 1
                words = "Sorry, I think I missed a digit. Could you say the whole number again, slowly?"
                return Reply(words, "ask_number", speech=words)
            elif caller_id and (AFFIRM.match(low) or re.search(r"\b(this|that|same) (number|one)\b", low)):
                flow["callback"] = caller_id
            elif QUESTION.search(low):
                self._msg.pop(s, None)
                return None
            else:
                words = "No problem. What's the best number for Alex to call?"
                return Reply(words, "ask_number", speech=words)
            if flow.get("body"):
                self._callbacks[s] = flow["callback"]
                return self._save_message(flow, s, caller_id, heard_back)
            flow["step"] = "body"
            self._callbacks[s] = flow["callback"]
            words = f"Got it, {heard_back}. And what would you like me to tell Alex?" if heard_back else \
                "Got it. And what would you like me to tell Alex?"
            return Reply(words, "ask_message", speech=words)
        if READBACK.search(low):
            r = self._read_back(s, caller_id)     # still taking the message: ask for it after
            words = r.speech.replace("Is that right?", "And what would you like me to tell Alex?")
            return Reply(words, "read_back", speech=words)
        flow["body"] = text.strip()
        return self._save_message(flow, s, caller_id)

    def _read_back(self, s: str, caller_id: str | None) -> Reply:
        """'Read my number back' / 'what's my booking code?': say what we have, slowly."""
        parts = []
        if s in self._names:
            parts.append(f"I have your name as {self._names[s]}")
        number = self._callbacks.get(s) or caller_id
        digits = re.sub(r"\D", "", number or "")
        if len(digits) >= 10:
            parts.append(f"your number as {spoken_number(digits)}")
        code = None
        if self._booked.get(s):
            row = next((b for b in self.store.active() if b["id"] == self._booked[s]), None)
            code = row and row.get("code")
        if self._booked.get(s):
            row = next((b for b in self.store.active() if b["id"] == self._booked[s]), None)
            if row:
                parts.append("your call with Alex on " + spoken_when(datetime.fromisoformat(row["starts"]), self.tz))
        if code:
            parts.append("your booking code as " + " ".join(spoken_number(c) for c in code))
        if not parts:
            words = "I don't have any details from you yet. Would you like to leave a message for Alex?"
            return Reply(words, "read_back", speech=words)
        text = ", and ".join(parts)
        words = text[0].upper() + text[1:] + ". Is that right?"
        return Reply(words, "read_back", speech=words)

    def _save_message(self, flow: dict, s: str, caller_id: str | None, heard_back: str | None = None) -> Reply:
        self._msg.pop(s, None)
        self._last_msg[s] = self.store.add_message(flow.get("name"), flow.get("callback"), flow["body"], caller_id=caller_id)
        if URGENT.search(flow["body"].lower()):
            self.store.mark_message(self._last_msg[s], "urgent")
        if self.on_message:
            try:
                self.on_message({"name": flow.get("name"), "callback": flow.get("callback"),
                                 "urgent": bool(URGENT.search(flow["body"].lower())), "language": "en"})
            except Exception:
                pass                              # a notifier problem never breaks the call
        at = f" at {heard_back}" if heard_back else ""
        words = (f"Got it, {flow.get('name') or 'thanks'}. I'll make sure Alex gets your message, and he'll call you "
                 f"back{at} as soon as he can. {ANYTHING_ELSE}")
        return Reply(words, "message_taken", speech=words)

    def _book(self, when: str, session_id: str, caller_id: str | None = None) -> Reply:
        ref = self._booked.get(session_id)
        if ref and self._moving.get(session_id):
            r = self.dialogue.handle(f"reschedule {ref} {when}", session_id, caller_id=caller_id)
            if r.action == "confirm_required":
                starts = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
                words = f"Sure, I can move it to {spoken_when(starts, self.tz)}. Should I go ahead?"
                return Reply(r.text, r.action, r.committed_id, speech=words)
            return r
        return self.dialogue.handle(f"book {when}", session_id, caller_id=caller_id)

    def _vary(self, r: Reply) -> Reply:
        sp = r.speech or ""
        for base, options in VARIANTS.items():
            if sp.startswith(base + " ") or sp == base:
                choice = self._rng.choice(options)
                return Reply(r.text, r.action, r.committed_id, speech=choice + sp[len(base):])
        return r

    def _handle(self, utterance: str, session_id: str, caller_id: str | None = None) -> Reply:
        text = " ".join(utterance.strip().split())
        # Words phone-quality ASR mishears for common booking words.
        for heard, meant in MISHEARD:
            text = heard.sub(meant, text)
        text = corrected(text)          # 'Tuesday, no, no, Wednesday' -> 'Wednesday'
        expecting_time = self._calls.get(session_id) is not None and bool(
            self._calls[session_id].offered or self._calls[session_id].date)
        if expecting_time:
            text = THREE_MISHEARD.sub("three ", text.lower())
        low = re.sub(r"[.!?,]+", "", text.lower()).strip()
        st = self._calls.setdefault(session_id, _Call())
        pending = session_id in self.dialogue._pending
        # Whisper garbage (a decode loop like "[inainain...") or nothing intelligible: ask again.
        if not re.search(r"[a-z]{2,}|[0-9]", low) or re.search(r"(\w{2,4})\1{3,}", low):
            return Reply("Sorry, I didn't catch that.", "repeat",
                         speech="Sorry, I didn't catch that. Could you say that again?")
        if self._lang.get(session_id) == "es":
            return self._spanish(utterance, low, session_id, caller_id)
        flow = self._msg.get(session_id)
        if flow and not closing(low):
            r = self._message_step(flow, text, low, session_id, caller_id)
            if r is not None:
                return r
        about_me = self._topic.get(session_id) == "receptionist" or "receptionist" in low
        if about_me and re.search(r"\b(how much|price|cost|charge|pricing|per month|a month)\b", low) and not HAGGLE.search(low):
            return Reply(RECEPTIONIST_PRICE, "faq", speech=RECEPTIONIST_PRICE)
        if about_me and re.search(r"\b(how (fast|quick|long)|set ?up|get started|up and running)\b", low):
            return Reply(RECEPTIONIST_SETUP, "knowledge", speech=RECEPTIONIST_SETUP)
        if CARD_PAY.search(low):
            return Reply(CARD_PAY_SPEECH, "knowledge", speech=CARD_PAY_SPEECH)
        if PORTFOLIO_Q.search(low) and not LEGIT.search(low) and not BOOK_WORDS.search(low):
            return Reply(PORTFOLIO_SPEECH, "knowledge", speech=PORTFOLIO_SPEECH)
        if LEGIT.search(low) and not BOOK_WORDS.search(low):
            return Reply(LEGIT_SPEECH, "knowledge", speech=LEGIT_SPEECH)
        if self._topic.get(session_id) == "wrong_number":
            words = ("This is LexMakesIt. We build websites for small businesses, so I can't help with medicine, I'm sorry. "
                     "The number for your pharmacy is on your pill bottle, or a family member can help you find it. "
                     "If it's an emergency, please call nine one one.")
            if words == self._last_speech.get(session_id):
                words = "I'm sorry, I can't reach the pharmacy from here. The number is on your pill bottle. Take care of yourself."
            return Reply(words, "wrong_number", speech=words)
        burned = [x for x in ROUND7 if "sorry that happened" in x[1]]
        for pat, words in burned + POLICY + ROUND8 + ROUND7:
            if pat.search(low):
                if "isn't the pharmacy" in words:
                    self._topic[session_id] = "wrong_number"
                return Reply(words, "knowledge", speech=words)
        if re.search(r"\b(what'?s|what is|give me|can i (get|have)) (the|your|his|alex'?s) (phone )?number\b", low):
            contact = (self.profile.get("faq") or {}).get("contact") if self.profile else None
            if contact:
                return Reply(contact, "faq", speech=contact)
        if HAGGLE.search(low) or (MONEY_WORRY.search(low) and re.search(r"\b(how much|cost|price|afford|be)\b", low)):
            return Reply(HAGGLE_SPEECH, "knowledge", speech=HAGGLE_SPEECH)
        if SOLO_Q.search(low):
            return Reply(SOLO_SPEECH, "knowledge", speech=SOLO_SPEECH)
        if PRODUCT_Q.search(low) and not self._booked.get(session_id):
            self._topic[session_id] = "receptionist"
            self._calls[session_id] = _Call()
            self.dialogue._pending.pop(session_id, None)
            return Reply(PITCH_SPEECH, "pitch", speech=PITCH_SPEECH)
        ref = self._booked.get(session_id)
        if ref and STATUS_Q.search(low):
            row = next((b for b in self.store.active() if b["id"] == ref), None)
            if row:
                when = spoken_when(datetime.fromisoformat(row["starts"]), self.tz)
                words = f"Yes, you're still booked for {when}. Nothing's been cancelled. Would you like to keep it?"
            else:
                words = "That one isn't on the calendar anymore. Would you like me to book a new time?"
            return Reply(words, "status", speech=words)
        if ref and CANCEL_IT.search(low):
            # Before the goodbye check: "just cancel it entirely, thanks bye" must cancel.
            self._calls[session_id] = _Call()
            if self.store.cancel(ref, caller_id=caller_id):
                self._booked.pop(session_id, None)
                words = f"Okay, it's cancelled. {ANYTHING_ELSE}"
                return Reply("Your appointment is cancelled.", "cancelled", speech=words)
        m = SAID_NAME.search(text)
        if m and re.match(r"^(?:my name is|my name'?s|this is)", m.group(0), re.I) and session_id not in self._names:
            got = self._name_from(m.group(0))
            if got:
                self._names[session_id] = got     # "this is Jordan" said up front: don't ask again later
        if closing(low) and not (pending and AFFIRM.match(low)):
            # "I gotta go" / "No thanks. Bye." / "God bless, goodbye": let them go, warmly.
            self._calls[session_id] = _Call()
            open_flow = self._msg.pop(session_id, None)
            name = self._names.get(session_id)
            words = f"No problem{', ' + name if name else ''}. Thanks for calling, and take care!"
            if open_flow and open_flow.get("callback"):
                open_flow["body"] = open_flow.get("body") or "Asked for a call back."
                open_flow["name"] = open_flow.get("name") or name
                self._save_message(open_flow, session_id, caller_id)
                words = (f"No problem{', ' + name if name else ''}. I'll have Alex call you back at "
                         f"{spoken_number(open_flow['callback'])}. Thanks for calling, and take care!")
            if "god bless" in low:
                words = f"God bless you too{', ' + name if name else ''}. Thanks for calling, and take care!"
            return Reply("Thanks for calling. Have a great day!", "goodbye", speech=words)
        if WAIT.match(low) and (len(low.split()) <= 5 or not QUESTION.search(WAIT.sub("", low).strip())) \
                and not any(vars(parse_when(re.sub(r"\b(one|a|1) (sec|second|moment|minute)\b", "", WAIT.sub("", low)), self.clock())).values()):
            return Reply("Take your time.", "filler", speech="Sure, take your time.")
        if READBACK.search(low):
            return self._read_back(session_id, caller_id)
        fixed = spoken_digits(text)
        if self._last.get(session_id) in ("read_back", "message_taken") and (len(fixed) == 10 or (len(fixed) == 11 and fixed[0] == "1")):
            self._callbacks[session_id] = fixed
            if self._last_msg.get(session_id):
                self.store.set_message_callback(self._last_msg[session_id], fixed)
            words = f"Thanks for catching that. I've got {spoken_number(fixed)}. {ANYTHING_ELSE}"
            return Reply(words, "number_fixed", speech=words)
        num, rest = phone_in(text)
        if num and pending and re.search(r"\b(call me|call me back|callback|call back|have (him|alex) call)\b", low):
            self.dialogue._pending.pop(session_id, None)
            self._calls[session_id] = _Call()
            pending = False
        if not num and pending and session_id not in self._msg and 7 <= len(spoken_digits(text)) <= 9 \
                and self._lead_name(phone_in(utterance)[1] if False else re.sub(r"[\d\s,.-]+$", "", utterance)):
            # "Jenny Park, 732 550 198": a yes with details, one digit lost on the line.
            name = self._lead_name(re.sub(r"[\d\s,.-]+$", "", utterance))
            r = self.dialogue.handle("yes", session_id, caller_id=caller_id)
            if r.action == "booked" and r.committed_id:
                self._booked[session_id] = r.committed_id
                self._names[session_id] = name
                self.store.set_name(r.committed_id, name, caller_id)
                body = (r.speech or r.text).replace(ANYTHING_ELSE, "").strip()
                words = (f"Thanks, {name}. {body} I think I missed a digit of your number, though. "
                         "Could you say it once more, slowly?")
                self._msg[session_id] = {"step": "number", "name": name, "body": "Booked a call.", "tries": 1}
                return Reply(r.text, r.action, r.committed_id, speech=words)
            return r
        if num and pending and session_id not in self._msg:
            # "Dana Morales, 973..." in answer to "want me to book it?": that's a yes, with details.
            name = self._lead_name(phone_in(utterance)[1]) or self._names.get(session_id)
            r = self.dialogue.handle("yes", session_id, caller_id=caller_id)
            self._callbacks[session_id] = num
            if r.action == "booked" and r.committed_id:
                self._booked[session_id] = r.committed_id
                if name:
                    self._names[session_id] = name
                    self.store.set_name(r.committed_id, name, caller_id)
                body = (r.speech or r.text).replace(ANYTHING_ELSE, "").strip()
                words = (f"Thanks{', ' + name if name else ''}. {body} Alex will call you at {spoken_number(num)}. "
                         f"{ANYTHING_ELSE}")
                return Reply(r.text, r.action, r.committed_id, speech=words)
            return r
        if num and session_id not in self._msg and not pending:
            name = self._lead_name(phone_in(utterance)[1]) or self._names.get(session_id)
            if name:
                self._names[session_id] = name
            self._callbacks[session_id] = num
            self._calls[session_id] = _Call()
            self._msg[session_id] = {"step": "body" if name else "name", "body": None, "name": name, "callback": num}
            if not name:
                words = f"Thanks, I've got {spoken_number(num)}. And what's your name?"
                return Reply(words, "ask_name", speech=words)
            words = f"Got it, {name}. I have {spoken_number(num)}. What would you like me to tell Alex?"
            return Reply(words, "ask_message", speech=words)
        if PRICE_SHAPE.search(low) and not re.search(r"\b(deposit|payment plans?|installments?|split)\b", low):
            return Reply(PRICE_SHAPE_SPEECH, "knowledge", speech=PRICE_SHAPE_SPEECH)
        if MONTHLY_Q.search(low):
            return Reply(MONTHLY_SPEECH, "knowledge", speech=MONTHLY_SPEECH)
        if AI_Q.search(low):
            return Reply(AI_SPEECH, "knowledge", speech=AI_SPEECH)
        if REPEAT_ASK.search(low) and self._last_speech.get(session_id):
            body = self._last_speech[session_id]
            first = split_first(body)
            if first in OPENERS and first != body:
                body = body[len(first):].strip()
            if re.search(r"\bslow", low):
                # "Slower, please": the same thing, shorter and simpler.
                parts = re.split(r"(?<=[.!?])\s+", body)
                body = " ".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else body)
                return Reply(body, "repeat_back", speech=f"Sure. {body}")
            return Reply(body, "repeat_back", speech=f"Of course. {body}")
        if SPANISH.search(low) or len(SPANISH_WORDS & set(low.split())) >= 2 \
                or len(set(m.group(0).lower() for m in SPANISH_GARBLE.finditer(low))) >= 3:
            # Spanish speakers are a big part of the customers: greet in Spanish and get a callback.
            self._msg.pop(session_id, None)
            self._lang[session_id] = "es"
            self._es[session_id] = {"first": text, "want": "details",
                                    "name": self._names.get(session_id), "callback": self._callbacks.get(session_id)}
            if self._last.get(session_id) and re.search(r"[?¿]|\b(puede|habla|cuánto|cuanto)\b", low):
                # Switched mid-call ("mira, mejor en español, ...?"): keep going and answer.
                r = self._spanish(utterance, low, session_id, caller_id)
                words = "Claro, seguimos en español. " + r.speech
                return Reply(words, "spanish", speech=words)
            words = SPANISH_LEAD if len(SPANISH_WORDS & set(low.split())) >= 2 else (
                "I speak English best right now, but I'm happy to help. Hablo un poco de español: "
                "Alex le puede llamar. ¿Me dice su nombre y su número, por favor?")
            return Reply(words, "spanish", speech=words)
        if WEATHER.search(low) and not BOOK_WORDS.search(low):
            return Reply("I hear you! What can I help you with today?", "small_talk",
                         speech="Oh, I hear you! What can I help you with today?")
        if SITE_TROUBLE.search(low) and not REAL_EMERGENCY.search(low) and not HYPOTHETICAL.search(low):
            return self._start_message(session_id, caller_id, body=text,
                                       lead="Oh no, I'm sorry about that. Let me take a message so Alex can get on it as soon as he can.")
        last = self._last.get(session_id)
        if (WHEN_CALL.search(low) or URGENT.search(low)) and self._last.get(session_id) == "message_taken":
            # Honest: no promised time. Urgent marks the message so Alex sees it first.
            if URGENT.search(low) and self._last_msg.get(session_id):
                self.store.mark_message(self._last_msg[session_id], "urgent")
            words = ("I've marked it urgent so Alex sees it first. I can't promise an exact time, but he'll call you "
                     "back as soon as he can.") if URGENT.search(low) else                     ("I can't promise an exact time, but Alex gets your message right away and calls back as soon as he "
                     "can. Want me to mark it urgent?")
            return Reply(words, "message_followup", speech=words)
        if MESSAGE.search(low) or (last in ("unknown", "handoff", "spanish") and re.search(r"\bmessage\b", low)) \
                or (last == "handoff" and AFFIRM.match(low)):
            return self._start_message(session_id, caller_id, lead="Of course, I can take a message for Alex.")
        if ref and MOVE.search(low):
            self._moving[session_id] = True
        if NO_BOOK.search(low) and (pending or st.offered or st.date):
            # "I just wanted to know the price first": stop offering times and answer.
            st = self._calls[session_id] = _Call()
            self.dialogue._pending.pop(session_id, None)
            pending = False
            text = re.sub(r"\b(book|booking|appointment|schedule)\w*\b", "", text, flags=re.I)
            low = re.sub(r"\b(book|booking|appointment|schedule)\w*\b", "", low)
            if not QUESTION.search(re.sub(NO_BOOK, "", low)) and not re.search(r"\b(price|cost|how much)\b", low):
                words = "No problem at all, there's no rush. What would you like to know?"
                return Reply(words, "reply", speech=words)
        # "one time or monthly" is a price question, not one o'clock.
        text = re.sub(r"\bone[- ]?time\b", "onetime", text, flags=re.I)
        low = re.sub(r"\bone[- ]?time\b", "onetime", low)
        if FILLER.match(low) or (not pending and re.fullmatch(r"(okay|ok)\W*(um+|uh+)\W*", low)):
            n = self._fillers.get(session_id, 0); self._fillers[session_id] = n + 1
            words = FILLER_REPLIES[n % 2]
            return Reply(words, "filler", speech=words)
        self._fillers[session_id] = 0
        if PRESENCE.search(low):
            return Reply(PRESENCE_REPLY, "presence", speech=PRESENCE_REPLY)
        offered_call = "free call" in (self._last_speech.get(session_id) or "")
        if offered_call and not pending and (AFFIRM.match(low) or re.search(r"\b(let'?s|sounds good|sure)\b", low)) \
                and not any(vars(parse_when(text, self.clock())).values()):
            # "Yeah, let's go over that" right after offering a free call: start booking it.
            self._last_speech[session_id] = None
            return self._handle("what times do you have", session_id, caller_id)
        st0 = self._calls.get(session_id)
        if OWN_BUSINESS.search(low) and not pending and not (st0 and st0.offered):
            return Reply(PITCH, "pitch", speech=PITCH)
        stripped = LEAD_IN.sub("", low).strip()
        if stripped != low and len(stripped.split()) >= 3:
            low, text = stripped, LEAD_IN.sub("", text.lower()).strip()
        if DATE_Q.search(low) and not parse_when(text, self.clock()).time:
            now = self.clock()
            day, _, clock = spoken_when(now).partition(" at ")
            return Reply(f"It's {day}, and it's {clock}.", "date",
                         speech=f"It's {day}, and it's about {clock}. What can I help you with?")
        if pending and AFFIRM.match(low):
            return self.dialogue.handle("yes", session_id, caller_id=caller_id)
        if IDENTITY_Q.search(low):
            words = self.knowledge.answer("real_person") if self.knowledge is not None else None
            return Reply(words or REAL_PERSON, "knowledge", speech=words or REAL_PERSON)
        # Waiting for yes/no and heard a short word that is neither (Ryan voice: "Yes." -> "Just"):
        # a person asks again rather than guessing either way.
        if pending and len(low.split()) <= 2 and not NEGATE.match(low) \
                and not any(vars(parse_when(text, self.clock())).values()) and not GOODBYE.search(low):
            return Reply("Sorry, was that a yes?", "confirm_again", speech="Sorry, was that a yes?")
        if NEGATE.match(low) and not any(vars(parse_when(text, self.clock())).values()):
            self._calls[session_id] = _Call()
            if pending:
                return self.dialogue.handle("no", session_id, caller_id=caller_id)
            return Reply("Okay. Is there anything else I can help with?", "reply",
                         speech="Okay. Is there anything else I can help with?")

        if closing(low):
            self._calls[session_id] = _Call()
            return Reply("Thanks for calling. Have a great day!", "goodbye",
                         speech="Okay. Thanks for calling. Have a great day!")
        if SMALL_TALK.search(low):
            return Reply("I'm doing well, thanks for asking! How can I help you today?", "small_talk",
                         speech="Okay. I'm doing well, thanks for asking! How can I help you today?")
        now = self.clock()
        if st.offered and AFFIRM.match(low) and not parse_when(text, now).time:
            return Reply("Which time works best?", "ask_which", speech="Sure. Which time works best for you?")
        if st.offered:
            pick_low = re.split(r"\b(?:no wait|wait no|no no|actually|i mean|sorry)\b\W*", low)[-1].strip() or low
            for word, i in ORDINALS.items():
                if re.search(ORDINAL_PICK.format(w=word), pick_low):
                    pick = st.offered[i]
                    return self._book(f"{pick:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
        bare = AFFIRM.match(low) and not pending and not any(vars(parse_when(text, now)).values())
        if bare and st.date and st.time:          # "yes" to a single time we just proposed
            return self._book(f"{datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}", session_id,
                                        caller_id=caller_id)
        if bare:
            return Reply("Great! What can I help you with?", "reply", speech="Okay. What can I help you with?")

        intent, conf = self.intents.classify(text)
        if AVAIL_Q.search(low):                   # "when are you free?" is a scheduling question
            intent, conf = "availability", max(conf, 0.9)
        elif TALK_TO.search(low) and not IDENTITY_Q.search(low) and not re.search(r"\b(book|schedule|set up)\b", low) \
                and not re.search(r"\b(am i|are you|is this)\b.*\b(human|person|real|bot|robot|ai)\b", low):
            intent, conf = "human", max(conf, 0.9)
        if intent in CUES and not CUES[intent].search(low):
            intent, conf = ("book", 0.5) if BOOK_WORDS.search(low) else ("faq_unknown", 0.3)
        if BOOK_WORDS.search(low) and (intent in ("book", "availability", "reschedule") or intent not in CUES):
            intent, conf = (intent if intent in ("availability", "reschedule") else "book"), max(conf, 0.5)
        when = parse_when(text, now)
        has_when = bool(when.date or when.time or when.part)
        # v2 model classes (conversational moves) -- regexes above already caught the clear cases.
        if conf >= 0.6 and not has_when:
            if intent == "affirm":
                if pending:
                    return self.dialogue.handle("yes", session_id, caller_id=caller_id)
                if st.date and st.time:
                    return self._book(f"{datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}",
                                                session_id, caller_id=caller_id)
                if st.offered:
                    return Reply("Which time works best?", "ask_which", speech="Sure. Which time works best for you?")
                return Reply("Great! What can I help you with?", "reply", speech="Okay. What can I help you with?")
            if intent == "negate":
                self._calls[session_id] = _Call()
                if pending:
                    return self.dialogue.handle("no", session_id, caller_id=caller_id)
                return Reply("Okay. Is there anything else I can help with?", "reply",
                             speech="Okay. Is there anything else I can help with?")
            if intent == "goodbye" and (closing(low) or len(low.split()) <= 6):
                self._calls[session_id] = _Call()
                return Reply("Thanks for calling. Have a great day!", "goodbye",
                             speech="Okay. Thanks for calling. Have a great day!")
            if intent == "small_talk":
                return Reply("I'm doing well, thanks for asking! How can I help you today?", "small_talk",
                             speech="Okay. I'm doing well, thanks for asking! How can I help you today?")
            if intent == "business":
                return self._faq(text)
        if OPEN_DAY.search(low) and when.date:
            return self._open_on(when.date)
        if LOCATION.search(low) and not has_when:
            return self._faq(text)

        if intent == "injection":
            self._calls[session_id] = _Call()
            return Reply("I can't do that, but I'm happy to help you book or answer a question.", "refuse",
                         speech="Sorry, I can't do that. But I'm happy to help you book, or answer a question.")
        if intent in ("human", "emergency"):
            if intent == "emergency" and REAL_EMERGENCY.search(low):
                return Reply("If this is an emergency, please hang up and dial 911.", "handoff",
                             speech="Okay. If this is an emergency, please hang up and dial nine one one.")
            if intent == "emergency":
                # "My site is down, it's an emergency" is urgent business, not a 911 call.
                return self._start_message(session_id, caller_id, body=text,
                                           lead="I'm sorry you're dealing with that. Let me take a message so Alex can get back to you as soon as he can.")
            # The direct line once, and a message as the easy option (simulated callers heard the
            # number five times a call and still couldn't leave a message).
            contact = (self.profile.get("faq") or {}).get("contact")
            if contact:
                words = f"Of course. {contact} Or if it's easier, I can take a message and have him call you back."
                return Reply(words, "handoff", speech=words)
            return Reply(TALK_TO_SPEECH, "handoff", speech=TALK_TO_SPEECH)
        if intent == "cancel" or low.startswith("cancel"):
            if re.search(r"\d", text) or any(w in low.split() for w in dr._UNITS):
                return self.dialogue.handle(low if low.startswith("cancel") else "cancel " + low, session_id,
                                            caller_id=caller_id)
            return Reply("What's your four digit booking code?", "ask_code",
                         speech="Sure. What's your four digit booking code?")
        booking_ask = intent in ("book", "availability", "reschedule") and BOOK_WORDS.search(low)
        if (intent in ("faq", "faq_unknown") or profile_answer(self.profile, text)[1]) and not has_when                 and not booking_ask:
            if intent == "faq_unknown" and not profile_answer(self.profile, text)[1]:
                return self._unknown()
            return self._faq(text)
        booking_turn = intent in ("book", "availability", "reschedule") and conf >= MIN_CONFIDENCE
        follow_up = has_when and (st.date or st.time or st.offered or pending)
        if not (booking_turn or follow_up or has_when):
            return Reply("That's outside what I can help with, but I can answer questions about LexMakesIt or book you a free call with Alex.", "unknown",
                         speech=UNKNOWN_SPEECH)

        # Merge what this turn said into what we already know.
        if when.date:
            st.date, st.offered = when.date, []
        if when.time:
            st.time = when.time
        if when.part:
            st.part = when.part
        if st.date and st.time:
            if self._open_at(st.date, st.time):
                starts = datetime.combine(st.date, st.time)
                r = self._book(f"{starts:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
                # "2 p.m. yes." -- picked the time and confirmed it in one breath.
                if r.action == "confirm_required" and CONFIRM_TAIL.search(low):
                    return self.dialogue.handle("yes", session_id, caller_id=caller_id)
                return r
            # Closed then: never a dead end -- offer that day's openings, or the next open day's.
            day = self._day_words(st.date, now.date())
            asked, st.time = st.time, None
            if self.hours.get(st.date.weekday()):
                note = f"We're closed at {spoken_time(datetime.combine(st.date, asked))} {day}."
            else:
                note = f"We're closed {day}."
                nxt = self._next_open_day(st.date)
                if nxt is None:
                    return Reply(note, "no_slots", speech=f"Hmm, okay. {note} Would another day work?")
                st.date, day = nxt, self._day_words(nxt, now.date())
                if self._open_at(nxt, asked):
                    st.time = asked
                    r = self._book(f"{datetime.combine(nxt, asked):%Y-%m-%d %H:%M}", session_id,
                                             caller_id=caller_id)
                    if r.action == "confirm_required":
                        tail = (r.speech or r.text).split(". ", 1)[1] if ". " in (r.speech or r.text) else r.speech
                        return Reply(r.text, r.action, r.committed_id, speech=f"Hmm, okay. {note} {tail}")
                    return r
            slots = self._free_slots(st.date, st.part)
            if not slots:
                return Reply(f"{note} Nothing else is open {day}.", "no_slots",
                             speech=f"Hmm, okay. {note} Would another day work?")
            st.offered = slots
            return Reply(f"{note} {day}: {self._list(slots)}", "offer_slots",
                         speech=f"Hmm, okay. {note} {day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
        if st.date:
            slots = self._free_slots(st.date, st.part)
            day = self._day_words(st.date, now.date())
            note = ""
            if not slots:
                note = f"We're closed {day}. " if not self.hours.get(st.date.weekday()) else f"{day[0].upper() + day[1:]} is full. "
                nxt = st.date
                for _ in range(7):
                    nxt = self._next_open_day(nxt)
                    if nxt is None:
                        break
                    slots = self._free_slots(nxt, st.part) or self._free_slots(nxt, None)
                    if slots:
                        break
                if not slots:
                    return Reply(f"{note}Nothing is open soon.", "no_slots",
                                 speech=f"Hmm, okay. {note}Would another day work?")
                st.date, day = nxt, self._day_words(nxt, now.date())
            st.offered = slots
            if note:
                return Reply(f"{note}{day}: {self._list(slots)}", "offer_slots",
                             speech=f"Hmm, okay. {note}{day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
            return Reply(f"{day}: {self._list(slots)}", "offer_slots",
                         speech=f"Sure. {day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
        if not VAGUE_PERIOD.search(low) and not st.date:
            day = now.date()
            for _ in range(8):
                slots = self._free_slots(day, st.part)
                if slots:
                    st.date, st.offered = day, slots
                    words = self._day_words(day, now.date())
                    return Reply(f"{words}: {self._list(slots)}", "offer_slots",
                                 speech=f"Sure. The soonest I have is {words} at {self._list(slots)}. "
                                        f"Or is there a day that works better?")
                nxt = self._next_open_day(day)
                if nxt is None:
                    break
                day = nxt
        return Reply("What day works for you?", "ask_day", speech="Sure, I can check. What day works for you?")

