from shadowmarket.economy import (
    initial_listing_price,
    ipo_cost,
    next_price,
    percent_change,
    weighted_average_buy_price,
)


def test_ipo_cost_scales_with_listings():
    assert ipo_cost(0) == 500
    assert ipo_cost(1) == 550
    assert ipo_cost(10) == 1000


def test_price_rises_with_volume():
    # P = 100 + (10 * 0.5) - (0.02 * 100) = 103.0
    assert next_price(100, 10) == 103.0


def test_price_floor():
    assert next_price(10.1, 0, decay_rate=0.5) == 10.0


def test_dead_server_pauses_decay():
    # With decay paused, unused stocks stay put instead of bleeding to the floor.
    assert next_price(100, 0, decay_paused=True) == 100.0
    assert next_price(100, 0, decay_paused=False) == 98.0


def test_weighted_average_buy_price():
    assert weighted_average_buy_price(2, 100, 2, 200) == 150
    assert weighted_average_buy_price(0, 0, 3, 80) == 80


def test_percent_change():
    assert percent_change(110, 100) == 10
    assert percent_change(90, 100) == -10
    assert percent_change(50, 0) == 0


def test_initial_price():
    assert initial_listing_price() == 100.0
