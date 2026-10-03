"""Sizing a position by the money spent, instead of by a share count.

"I opened a position with 3000 egp at a price of 10" is 300 shares. The model is
forbidden any arithmetic (rule 2), so it records the amount and the price and the
backend divides them — on the real numbers, and rounded down, because an order
cannot buy part of a share.
"""

from conftest import approve, proposal, record_text, set_proposal, trade


def _open_with_amount(client, amount: str, price: str, **extra):
    set_proposal(
        client,
        proposal(
            [{"kind": "OPEN", "amount": amount, "price": price, **extra}],
            symbol="NVDA",
        ),
    )
    note = record_text(client, "I opened a position")
    return approve(client, note["id"])


def test_the_money_and_the_price_give_the_share_count(client):
    result = _open_with_amount(client, "3000", "10")
    assert result.status_code == 200, result.text

    detail = trade(client, result.json()["trade_id"])

    assert detail["shares"] == "300"
    # What he said is kept beside the count.
    assert detail["notes"][0]["changes"][0]["amount"] == "3000"


def test_the_count_is_rounded_down_to_a_whole_share(client):
    """3000 at 7.35 is 408.16 shares, and an order cannot buy the .16.

    Rounding up would spend more money than the trader said he had, so the count
    goes down and he spends a little less than he named.
    """
    result = _open_with_amount(client, "3000", "7.35")
    assert result.status_code == 200, result.text

    detail = trade(client, result.json()["trade_id"])

    assert detail["shares"] == "408"
    assert detail["average_price"] == "7.35"


def test_a_stated_share_count_wins_over_an_amount(client):
    """If he says both, the count he said is the count — never recomputed."""
    result = _open_with_amount(client, "3000", "10", shares="250")
    assert result.status_code == 200, result.text

    detail = trade(client, result.json()["trade_id"])

    assert detail["shares"] == "250"


def test_an_amount_without_a_price_is_still_incomplete(client):
    """The division needs a price. With no price the note is refused the same way
    any note with a missing number is, so the review screen asks for it."""
    result = _open_with_amount(client, "3000", None) if False else None
    set_proposal(
        client,
        proposal([{"kind": "OPEN", "amount": "3000"}], symbol="NVDA"),
    )
    note = record_text(client, "I opened a position with 3000")
    refused = approve(client, note["id"])

    assert refused.status_code >= 400
