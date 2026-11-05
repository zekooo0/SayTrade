"""Portfolios: one market each, with its own currency and its own capital.

The point is that markets never mix. A note is recorded *into* a market, the
trade it opens lands in that same market, and a position is sized against that
market's money — not against another market's.
"""

from decimal import Decimal

from conftest import (
    approve,
    auth_headers,
    drain,
    open_proposal,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trade,
)


def make_portfolio(
    client, name="Egypt", currency="EGP", capital=None, sub="user_test1"
) -> dict:
    body = client.post(
        "/portfolios",
        headers=auth_headers(sub),
        json={"name": name, "currency": currency},
    ).json()
    if capital is not None:
        body = client.patch(
            f"/portfolios/{body['id']}",
            headers=auth_headers(sub),
            json={"capital": capital},
        ).json()
    return body


def test_a_new_trader_has_no_portfolios(client):
    assert client.get("/portfolios", headers=auth_headers()).json() == []


def test_only_the_egyptian_and_us_markets_can_be_created(client):
    """The product trades EGX and US only: a market's symbols are loaded per
    exchange, so a third one would be a book nothing can ever resolve."""
    egx = client.post(
        "/portfolios",
        headers=auth_headers(),
        json={"name": "Egypt", "exchange": "EGX"},
    ).json()
    assert (egx["exchange"], egx["currency"]) == ("EGX", "EGP")

    us = client.post(
        "/portfolios",
        headers=auth_headers(),
        json={"name": "US", "exchange": "US"},
    ).json()
    assert (us["exchange"], us["currency"]) == ("US", "USD")

    for body in (
        {"name": "London", "exchange": "LSE"},
        {"name": "Tokyo", "currency": "JPY"},
        {"name": "Nowhere", "exchange": ""},
    ):
        refused = client.post("/portfolios", headers=auth_headers(), json=body)
        assert refused.status_code == 422, refused.text
        assert "EGX" in refused.json()["detail"]
        assert "US" in refused.json()["detail"]


def test_a_market_can_be_created_with_its_capital(client):
    """The new-market dialog asks for the money in the same request: one trip,
    and the exposure block has a number from the first trade."""
    resp = client.post(
        "/portfolios",
        headers=auth_headers(),
        json={"name": "Egypt", "exchange": "EGX", "capital": "250000"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["exchange"], body["currency"]) == ("EGX", "EGP")
    assert Decimal(body["capital"]) == Decimal("250000")


def test_a_market_cannot_be_changed_to_an_unsupported_exchange(client):
    body = make_portfolio(client)

    for patch in ({"exchange": "NASDAQ"}, {"currency": "EUR"}):
        refused = client.patch(
            f"/portfolios/{body['id']}", headers=auth_headers(), json=patch
        )
        assert refused.status_code == 422, refused.text

    unchanged = client.get("/portfolios", headers=auth_headers()).json()[0]
    assert (unchanged["exchange"], unchanged["currency"]) == ("EGX", "EGP")


def test_a_portfolio_holds_its_own_currency_and_capital(client):
    body = make_portfolio(client, capital="100000")

    assert body["name"] == "Egypt"
    assert body["currency"] == "EGP"
    assert Decimal(body["capital"]) == Decimal("100000")


def test_the_capital_can_be_cleared(client):
    body = make_portfolio(client, capital="100000")

    cleared = client.patch(
        f"/portfolios/{body['id']}",
        headers=auth_headers(),
        json={"clear_capital": True},
    ).json()

    assert cleared["capital"] is None


def test_a_note_recorded_into_a_portfolio_opens_its_trade_there(client):
    """The market never mixes: the trade lands in the portfolio its note was
    recorded into."""
    egypt = make_portfolio(client)
    set_proposal(client, open_proposal(symbol="SWDY"))

    note = record_text(
        client, "I opened a position in SWDY", portfolio_id=egypt["id"]
    )
    result = approve(client, note["id"])
    assert result.status_code == 200, result.text

    opened = trade(client, result.json()["trade_id"])
    assert opened["portfolio_id"] == egypt["id"]


def test_a_position_is_sized_against_its_own_markets_capital(client):
    egypt = make_portfolio(client, capital="100000")
    set_proposal(client, open_proposal(symbol="SWDY"))
    note = record_text(
        client, "I opened a position in SWDY", portfolio_id=egypt["id"]
    )
    opened = trade(client, approve(client, note["id"]).json()["trade_id"])

    cost = Decimal(opened["shares"]) * Decimal(opened["average_price"])
    expected = (cost / Decimal("100000") * 100).quantize(Decimal("0.0001"))
    assert Decimal(opened["position_size_percent"]) == expected


def test_another_markets_capital_does_not_size_this_trade(client):
    """A US trade is not sized by the Egyptian capital. A market with no capital
    of its own leaves the size unknown — never somebody else's number."""
    make_portfolio(client, name="Egypt", currency="EGP", capital="100000")
    us = make_portfolio(client, name="US", currency="USD")  # no capital yet
    set_proposal(client, open_proposal(symbol="NVDA"))

    note = record_text(client, "I opened a position in NVDA", portfolio_id=us["id"])
    opened = trade(client, approve(client, note["id"]).json()["trade_id"])

    assert opened["portfolio_id"] == us["id"]
    assert opened["position_size_percent"] is None


def test_a_portfolio_can_be_renamed(client):
    body = make_portfolio(client)

    renamed = client.patch(
        f"/portfolios/{body['id']}",
        headers=auth_headers(),
        json={"name": "Egypt swing"},
    ).json()

    assert renamed["name"] == "Egypt swing"
    assert renamed["currency"] == "EGP"  # untouched: an absent field is left alone


def test_the_trades_list_scopes_to_one_market(client):
    """What the switch in the app sends: one market at a time."""
    egypt = make_portfolio(client, name="Egypt", currency="EGP")
    us = make_portfolio(client, name="US", currency="USD")

    set_proposal(client, open_proposal(symbol="SWDY"))
    swdy = record_text(client, "I opened a position in SWDY", portfolio_id=egypt["id"])
    approve(client, swdy["id"])

    set_proposal(client, open_proposal(symbol="NVDA"))
    nvda = record_text(client, "I opened a position in NVDA", portfolio_id=us["id"])
    approve(client, nvda["id"])

    egypt_only = client.get(
        f"/trades?portfolio_id={egypt['id']}", headers=auth_headers()
    ).json()
    assert [t["symbol"] for t in egypt_only] == ["SWDY"]

    # Without the filter the trader still sees everything they own.
    everything = client.get("/trades", headers=auth_headers()).json()
    assert sorted(t["symbol"] for t in everything) == ["NVDA", "SWDY"]


def test_a_note_with_no_market_and_no_trade_is_refused(client):
    """The rule: a market comes first.

    Without one, the trade the note opens would belong to no market — and
    markets never mix, so there is nothing sensible to do with it.
    """
    resp = client.post(
        "/notes/text",
        headers=auth_headers(),
        json={"text": "I opened a position in NVDA"},
    )

    assert resp.status_code == 422, resp.text
    assert "market" in resp.json()["detail"].lower()


def test_a_follow_up_needs_no_market_of_its_own(client):
    """A follow-up already belongs to a trade, and that trade belongs to a
    market — so it needs none of its own."""
    egypt = make_portfolio(client)
    set_proposal(client, open_proposal(symbol="SWDY"))
    first = record_text(
        client, "I opened a position in SWDY", portfolio_id=egypt["id"]
    )
    trade_id = approve(client, first["id"]).json()["trade_id"]

    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}]))
    follow_up = record_text(client, "I moved the stop to 9.5", trade_id=trade_id)

    assert follow_up["trade_id"] == trade_id
    assert follow_up["portfolio_id"] is None  # the trade carries the market


def make_trade(client, symbol: str, portfolio_id: str) -> str:
    """Open a trade in a market, through the API. Returns its id."""
    set_proposal(client, open_proposal(symbol=symbol))
    note = record_text(
        client, f"I opened a position in {symbol}", portfolio_id=portfolio_id
    )
    return approve(client, note["id"]).json()["trade_id"]


def test_the_extractor_is_told_which_market_the_note_belongs_to(client):
    """The AI reads a note with the market in hand: its name, its currency, and
    the tickers already held there. That is what stops a misheard ticker from
    becoming a confident symbol in the wrong market."""
    from conftest import extractor_calls

    egypt = make_portfolio(client, name="Egypt", currency="EGP")
    make_trade(client, "SWDY", egypt["id"])

    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}]))
    record_text(client, "I moved the stop to 9.5", portfolio_id=egypt["id"])

    market = extractor_calls()[-1]["market"]
    assert market is not None
    assert market.name == "Egypt"
    assert market.currency == "EGP"
    assert market.symbols == ("SWDY",)


def test_a_note_resolves_only_within_its_own_market(client):
    """One open trade in each market used to read as ambiguous. A note recorded
    in Egypt has exactly one candidate: the Egyptian trade."""
    egypt = make_portfolio(client, name="Egypt", currency="EGP")
    us = make_portfolio(client, name="US", currency="USD")
    make_trade(client, "SWDY", egypt["id"])
    make_trade(client, "NVDA", us["id"])

    # A change note in Egypt that names no symbol: one candidate, in Egypt.
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}]))
    note = record_text(client, "I moved the stop to 9.5", portfolio_id=egypt["id"])

    assert note["symbol"] == "SWDY"
    assert note["symbol_candidates"] is None


def test_a_ticker_from_another_market_is_dropped(client):
    """The model's reading is checked against the market, not shown.

    The trader's Egyptian note once came back as MTSS — a Russian telecom —
    because nothing checked what the model read against what actually trades in
    Egypt. Now the market's own list decides: MTSS is not on it, so the symbol
    comes back empty and the review screen asks, instead of asserting.
    """
    egypt = make_portfolio(client, name="EGYPT", currency="EGP")
    make_trade(client, "SWDY", egypt["id"])

    set_proposal(client, open_proposal(symbol="MTSS"))
    note = record_text(client, "I bought Elsewedy", portfolio_id=egypt["id"])

    assert note["symbol"] is None  # MTSS does not trade in Egypt
    assert note["market_symbols"] == ["SWDY"]  # the market's own tickers



def test_the_transcriber_is_told_the_markets_tickers(client):
    """The decoder hints reach the transcriber.

    The trader's own recording lost its share count in the transcript: whisper-1
    read "مية سهم" as "ميت ساهمة". gpt-transcribe reads it correctly, and reads
    it exactly once the market's tickers and the Arabic number words are handed
    to it as keywords.
    """
    from app.main import app

    egypt = make_portfolio(client, name="EGYPT", currency="EGP")
    make_trade(client, "SWDY", egypt["id"])

    resp = post_voice(client, portfolio_id=egypt["id"])
    drain(client, resp.json()["id"])

    call = app.state.transcriber.calls[-1]
    assert "SWDY" in call["keywords"]  # the tickers held in that market
    assert "مية" in call["keywords"]  # the Egyptian Arabic number words
    assert call["languages"] == ["ar", "en"]
