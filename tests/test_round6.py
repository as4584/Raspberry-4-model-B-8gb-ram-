"""Round 6: Spanish callers lost at the details, Square became a booking, a short number lost a booking."""
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


ES = "Hola, buenas, necesito una página web para mi panadería"


def test_spanish_name_without_me_llamo(brain):
    r = talk(brain, ES, "Carmen Rosario, Panadería La Dominicana")
    assert "carmen" in said(r[1])


def test_spanish_short_number_asked_again_then_caller_id(brain):
    r = talk(brain, ES, "Carmen Rosario", "nueve siete tres cinco cinco uno dos tres cuatro", "973 55 1234")
    assert "repet" in said(r[2]) or "despacio" in said(r[2])
    assert "disculpe, no le entendí" not in said(r[3])


def test_spanish_goodbyes(brain):
    for bye in ("Bueno, cuídese, que Dios le bendiga", "Gracias, cuídate"):
        r = talk(brain, ES, bye)
        assert r[-1].action == "goodbye", bye


def test_spanish_answers(brain):
    r = talk(brain, ES, "¿Cuánto tiempo tarda?", "¿Alex habla español?", "¿Cuánto cuesta la recepcionista?",
             "¿Funciona con Square?", "¿Se pueden hacer pedidos en línea?")
    assert "veinticuatro" in said(r[1])
    assert "español" in said(r[2])
    assert "flexible" in said(r[3]) or "tamaño" in said(r[3])
    assert "square" in said(r[4]) or "conectar" in said(r[4])
    assert "pedidos" in said(r[5])


def test_spanish_never_loops_the_same_line(brain):
    r = talk(brain, ES, "blorf", "zibba", "quux")
    texts = [said(x) for x in r[1:]]
    assert len(set(texts)) == len(texts) or any("mensaje" in t for t in texts[1:])


def test_switching_to_spanish_mid_call_keeps_going(brain):
    r = talk(brain, "Hi, I have a taco truck", "Mira, mejor en español. ¿La recepcionista puede contestar en español?")
    assert "sí" in said(r[1]) and "español" in said(r[1])


def test_square_is_not_a_booking(brain):
    r = talk(brain, "Can it answer my phone?", "Does it work with Square?")
    assert r[1].action not in ("offer_slots", "confirm_required") and "absolutely" not in said(r[1])


def test_short_number_at_confirm_still_books(brain):
    r = talk(brain, "Can I book something Friday at 10?", "Jenny Park, 732 550 198")
    assert brain.store.active()
    assert "number" in said(r[1])


def test_deposit_leads_with_the_deposit(brain):
    r = talk(brain, "Do you need a deposit?")
    assert said(r[0]).split("deposit")[0].count("payment plan") == 0
