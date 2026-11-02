from footy.names import best_match, canonical


def test_aliases_line_up_across_providers():
    assert canonical("Man United") == canonical("Manchester United") == canonical("Manchester Utd")
    assert canonical("Nott'm Forest") == canonical("Nottingham Forest")
    assert canonical("Wolves") == canonical("Wolverhampton Wanderers")
    assert canonical("Newcastle") == canonical("Newcastle United")
    assert canonical("Bayern Munich") == canonical("FC Bayern München")
    assert canonical("Paris Saint-Germain") == canonical("Paris SG")
    assert canonical("Brighton & Hove Albion") == canonical("Brighton")
    assert canonical("Dagenham & Redbridge") == canonical("Dag and Red")
    assert canonical("Sheffield Weds") == canonical("Sheffield Wednesday")


def test_best_match_fuzzy():
    known = ["bolton", "wigan", "bristol city", "bristol rvs", "stockport"]
    assert best_match("Bolton Wanderers FC", known) == "bolton"
    assert best_match("Stockport County", known) == "stockport"
    assert best_match("Bristol Rovers", known) == "bristol rvs"
    assert best_match("Arsenal", known) is None
