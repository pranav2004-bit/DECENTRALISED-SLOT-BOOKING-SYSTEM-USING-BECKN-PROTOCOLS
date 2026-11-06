"""Search page's location picker: the postal_directory-backed endpoints
(divisions-by-state, division+state-by-pincode) and the "Detect my location"
reverse-geocode endpoint.
"""

import pytest
import responses
from django.test import Client
from django.urls import reverse
from postal_directory.models import Circle, Division, Pincode


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
def test_postal_divisions_view_returns_real_divisions_for_a_seeded_state(client):
    circle = Circle.objects.create(name="Andhra Pradesh Circle")
    Division.objects.create(circle=circle, name="Visakhapatnam Division")
    Division.objects.create(circle=circle, name="Vijayawada Division")

    resp = client.get(reverse("postal-divisions"), {"state": "Andhra Pradesh"})

    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["divisions"]]
    assert names == ["Vijayawada Division", "Visakhapatnam Division"]


@pytest.mark.django_db
def test_postal_divisions_view_returns_an_empty_list_for_an_unseeded_state(client):
    resp = client.get(reverse("postal-divisions"), {"state": "Kerala"})

    assert resp.status_code == 200
    assert resp.json() == {"divisions": []}


@pytest.mark.django_db
def test_postal_divisions_view_rejects_an_unrecognised_state(client):
    resp = client.get(reverse("postal-divisions"), {"state": "Narnia"})

    assert resp.status_code == 400


@pytest.mark.django_db
def test_postal_lookup_by_pincode_resolves_division_and_state(client):
    circle = Circle.objects.create(name="Andhra Pradesh Circle")
    division = Division.objects.create(circle=circle, name="Visakhapatnam Division")
    Pincode.objects.create(division=division, code="530001")

    resp = client.get(reverse("postal-lookup"), {"pincode": "530001"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["division"]["name"] == "Visakhapatnam Division"
    assert body["state"] == "Andhra Pradesh"


@pytest.mark.django_db
def test_postal_lookup_by_pincode_404s_for_an_unseeded_pincode(client):
    resp = client.get(reverse("postal-lookup"), {"pincode": "000000"})

    assert resp.status_code == 404


@pytest.mark.django_db
def test_geo_reverse_view_returns_the_real_pincode_from_the_geocoding_response(
    settings, client
):
    settings.GEOCODING_BASE_URL = "https://geocode.example"

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://geocode.example/reverse",
            json={"address": {"postcode": "530001", "state": "Andhra Pradesh"}},
            status=200,
        )
        resp = client.get(reverse("geo-reverse"), {"lat": "17.6868", "lon": "83.2185"})

    assert resp.status_code == 200
    assert resp.json() == {"pincode": "530001"}


@pytest.mark.django_db
def test_geo_reverse_view_returns_null_pincode_when_the_location_has_none(settings, client):
    settings.GEOCODING_BASE_URL = "https://geocode.example"

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://geocode.example/reverse",
            json={"address": {}},
            status=200,
        )
        resp = client.get(reverse("geo-reverse"), {"lat": "0", "lon": "0"})

    assert resp.status_code == 200
    assert resp.json() == {"pincode": None}


@pytest.mark.django_db
def test_geo_reverse_view_rejects_non_numeric_coordinates(client):
    resp = client.get(reverse("geo-reverse"), {"lat": "not-a-number", "lon": "83"})

    assert resp.status_code == 400


@pytest.mark.django_db
def test_geo_reverse_view_returns_502_when_the_geocoding_service_is_unreachable(
    settings, client
):
    settings.GEOCODING_BASE_URL = "https://geocode.example"

    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://geocode.example/reverse", status=500)
        resp = client.get(reverse("geo-reverse"), {"lat": "17.6868", "lon": "83.2185"})

    assert resp.status_code == 502
