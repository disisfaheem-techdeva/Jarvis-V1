"""Ad wording follows what is sold and where the button goes: a product is
ordered, a service is booked, and the clinic's WhatsApp wording is unchanged."""
from app.adpilot import template_copy


def meta(kind, dest, price=2600):
    return template_copy("Test Grill", {"name": "Seekh Kabab Box", "price": price, "kind": kind}, "lahore", dest)["meta"]


def test_product_to_website_is_ordered_with_real_price():
    m = meta("product", "website")
    assert "PKR 2,600" in m["primary_text"] and "Order now" in m["primary_text"]
    assert "book" not in m["primary_text"].lower() and "visit" not in m["primary_text"].lower()
    assert m["description"] == "Order now" and "OrderNow" in m["hashtags"] and "BookNow" not in m["hashtags"]


def test_product_to_whatsapp_is_ordered_on_whatsapp():
    m = meta("product", "whatsapp")
    assert "WhatsApp to order" in m["primary_text"] and m["description"] == "Order on WhatsApp"


def test_service_to_website_is_booked():
    m = meta("service", "website")
    assert "book" in m["primary_text"].lower() and "BookNow" in m["hashtags"] and "OrderNow" not in m["hashtags"]


def test_clinic_whatsapp_wording_unchanged():
    m = template_copy("The Beauty Aesthetic", {"name": "Hair PRP", "price": 12000}, "lahore")["meta"]
    assert m["primary_text"] == ("Looking for Hair PRP in Lahore? The Beauty Aesthetic offers Hair PRP from "
                                 "PKR 12,000. Message us on WhatsApp to book your visit.")
    assert m["description"] == "Book on WhatsApp"
