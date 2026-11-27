"""After a Spanish greeting the whole call stays in Spanish: name and number, the message,
the price, the deposit, and a warm goodbye. The message Alex gets is complete."""
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def test_full_spanish_message(brain):
    r = talk(brain, "Hola, bueno, necesito una página web", "Me llamo Marisol Peña, mi número es 973 555 2281",
             "Quiero una página para mi restaurante con pedidos en línea", "No, gracias, adiós")
    assert "marisol" in said(r[1]) and "nueve siete tres" in said(r[1])
    assert "alex" in said(r[2]) and "llamar" in said(r[2])
    assert r[3].action == "goodbye" and "gracias" in said(r[3])
    m = brain.store.messages()[0]
    assert m["callback"] == "9735552281" and m["name"] == "Marisol Peña" and "pedidos" in m["body"]


def test_spanish_price_and_deposit(brain):
    r = talk(brain, "Hola, necesito una página web", "¿Cuánto cuesta?", "¿Hay que pagar un depósito?")
    assert "mil dólares" in said(r[1]) and "cincuenta por ciento" in said(r[2])


def test_spanish_never_falls_back_to_english(brain):
    r = talk(brain, "Hola, necesito ayuda", "blorf zibba")
    assert "sorry" not in said(r[1]) and "disculpe" in said(r[1])


def test_english_ears_garbling_spanish_is_still_spanish(brain):
    # What base.en made of "Hola, bueno, mira, necesito una página web para mi restaurante" on the Pi.
    r = talk(brain, "Ola, Fiono, Mira, Nesocito una pajino whi paramiras tarante.")
    assert r[0].action == "spanish"


def test_garbled_spanish_is_never_hung_up_on(brain):
    r = talk(brain, "Qanto Kusta.", "Hey Qui-Pagarin de Bizzito.", "Quiro Una Pajina-Khan Padido")
    assert not any(x.action == "hangup" for x in r)
