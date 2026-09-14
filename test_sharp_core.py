"""
Pytest tests for Sharp Signal V2 Core Module
"""

import pytest
from sharp_core import (
    parse_team_code,
    parse_spread,
    parse_total,
    parse_american_odds,
    american_odds_to_break_even_probability,
    impute_missing_percentage,
    calculate_money_minus_bets_screen,
    compare_lines,
    compare_total_lines,
    validate_percentages,
    DataQualityFlags,
)


class TestParseTeamCode:
    """Test team code parsing including 4-char and hyphenated codes."""
    
    def test_two_char_code(self):
        """Test standard 2-character team codes."""
        code, flags = parse_team_code("NY")
        assert code == "NY"
        assert not flags.malformed_team_code
    
    def test_three_char_code(self):
        """Test standard 3-character team codes."""
        code, flags = parse_team_code("NFL")
        assert code == "NFL"
        assert not flags.malformed_team_code
    
    def test_four_char_code_utsa(self):
        """Test 4-character team code UTSA."""
        code, flags = parse_team_code("UTSA")
        assert code == "UTSA"
        assert not flags.malformed_team_code
    
    def test_four_char_code_txst(self):
        """Test 4-character team code TXST."""
        code, flags = parse_team_code("TXST")
        assert code == "TXST"
        assert not flags.malformed_team_code
    
    def test_hyphenated_code_m_oh(self):
        """Test hyphenated team code M-OH."""
        code, flags = parse_team_code("M-OH")
        assert code == "M-OH"
        assert not flags.malformed_team_code
    
    def test_hyphenated_code_lowercase(self):
        """Test hyphenated code converts to uppercase."""
        code, flags = parse_team_code("m-oh")
        assert code == "M-OH"
        assert not flags.malformed_team_code
    
    def test_invalid_code_too_long(self):
        """Test that 5+ character codes without hyphen are flagged."""
        code, flags = parse_team_code("TOOLONG")
        assert flags.malformed_team_code
    
    def test_invalid_code_with_numbers(self):
        """Test that codes with numbers are flagged."""
        code, flags = parse_team_code("NY1")
        assert flags.malformed_team_code
    
    def test_empty_code(self):
        """Test that empty strings are flagged."""
        code, flags = parse_team_code("")
        assert flags.malformed_team_code
    
    def test_none_code(self):
        """Test that None is flagged."""
        code, flags = parse_team_code(None)
        assert flags.malformed_team_code


class TestParseSpread:
    """Test spread parsing for consensus and best-available lines."""
    
    def test_simple_spread(self):
        """Test basic spread parsing."""
        spread, flags = parse_spread("-5.5 / +5.5")
        assert spread == (-5.5, 5.5)
        assert not flags.missing_spread
    
    def test_integer_spread(self):
        """Test spread without decimals."""
        spread, flags = parse_spread("-3 / +3")
        assert spread == (-3.0, 3.0)
        assert not flags.missing_spread
    
    def test_spread_with_extra_chars(self):
        """Test spread extraction ignores extra characters."""
        spread, flags = parse_spread("Away -5.5 Home +5.5")
        assert spread == (-5.5, 5.5)
        assert not flags.missing_spread
    
    def test_missing_spread(self):
        """Test handling of missing spread."""
        spread, flags = parse_spread("N/A")
        assert spread is None
        assert flags.missing_spread
    
    def test_empty_spread(self):
        """Test handling of empty spread string."""
        spread, flags = parse_spread("")
        assert spread is None
        assert flags.missing_spread
    
    def test_none_spread(self):
        """Test handling of None spread."""
        spread, flags = parse_spread(None)
        assert spread is None
        assert flags.missing_spread
    
    def test_malformed_spread_no_numbers(self):
        """Test handling of malformed spread with no numbers."""
        spread, flags = parse_spread("ABC / XYZ")
        assert spread is None
        assert flags.missing_spread

    @pytest.mark.parametrize("value", ["PK / PK", "PICK / PICK", "PICK'EM / PICK'EM"])
    def test_pickem_spread(self, value):
        spread, flags = parse_spread(value)
        assert spread == (0.0, 0.0)
        assert not flags.has_issues()


class TestParseTotal:
    """Test total/over-under parsing."""
    
    def test_over_with_decimal(self):
        """Test over total with decimal."""
        total, flags = parse_total("o45.5")
        assert total == 45.5
        assert not flags.missing_total
    
    def test_under_with_decimal(self):
        """Test under total with decimal."""
        total, flags = parse_total("u45.5")
        assert total == 45.5
        assert not flags.missing_total
    
    def test_over_uppercase(self):
        """Test over total uppercase."""
        total, flags = parse_total("O45.5")
        assert total == 45.5
        assert not flags.missing_total
    
    def test_under_uppercase(self):
        """Test under total uppercase."""
        total, flags = parse_total("U45.5")
        assert total == 45.5
        assert not flags.missing_total
    
    def test_integer_total(self):
        """Test total without decimal."""
        total, flags = parse_total("o42")
        assert total == 42.0
        assert not flags.missing_total
    
    def test_total_with_parentheses(self):
        """Test total with parentheses."""
        total, flags = parse_total("(o45.5)")
        assert total == 45.5
        assert not flags.missing_total
    
    def test_missing_total(self):
        """Test handling of missing total."""
        total, flags = parse_total("N/A")
        assert total is None
        assert flags.missing_total
    
    def test_empty_total(self):
        """Test handling of empty total."""
        total, flags = parse_total("")
        assert total is None
        assert flags.missing_total


class TestParseAmericanOdds:
    """Test American odds parsing."""
    
    def test_negative_odds_favorite(self):
        """Test negative odds (favorite)."""
        odds, flags = parse_american_odds("-110")
        assert odds == -110
        assert not flags.invalid_odds
    
    def test_positive_odds_underdog(self):
        """Test positive odds (underdog)."""
        odds, flags = parse_american_odds("+150")
        assert odds == 150
        assert not flags.invalid_odds
    
    def test_positive_odds_without_plus(self):
        """Test positive odds without plus sign."""
        odds, flags = parse_american_odds("150")
        assert odds == 150
        assert not flags.invalid_odds
    
    def test_even_money_odds(self):
        """Test even money odds."""
        odds, flags = parse_american_odds("-100")
        assert odds == -100
        assert not flags.invalid_odds

    @pytest.mark.parametrize("value", ["even", "EVEN", "ev", "EV"])
    def test_text_even_money_odds(self, value):
        odds, flags = parse_american_odds(value)
        assert odds == 100
        assert not flags.invalid_odds
    
    def test_odds_with_suffix(self):
        """Test odds with suffix like -110EV."""
        odds, flags = parse_american_odds("-110EV")
        assert odds == -110
        assert not flags.invalid_odds
    
    def test_missing_odds(self):
        """Test handling of missing odds."""
        odds, flags = parse_american_odds("N/A")
        assert odds is None
        assert flags.invalid_odds
    
    def test_empty_odds(self):
        """Test handling of empty odds."""
        odds, flags = parse_american_odds("")
        assert odds is None
        assert flags.invalid_odds
    
    def test_zero_odds_invalid(self):
        """Test that zero odds are invalid."""
        odds, flags = parse_american_odds("0")
        assert odds is None
        assert flags.invalid_odds


class TestAmericanOddsToBreakEvenProbability:
    """Test conversion of American odds to break-even probability."""
    
    def test_negative_odds_favorite(self):
        """Test break-even probability for favorite (-110)."""
        prob = american_odds_to_break_even_probability(-110)
        assert pytest.approx(prob, 0.01) == 0.524
    
    def test_negative_odds_heavy_favorite(self):
        """Test break-even probability for heavy favorite (-200)."""
        prob = american_odds_to_break_even_probability(-200)
        assert pytest.approx(prob, 0.01) == 0.667
    
    def test_positive_odds_underdog(self):
        """Test break-even probability for underdog (+150)."""
        prob = american_odds_to_break_even_probability(150)
        assert pytest.approx(prob, 0.01) == 0.400
    
    def test_positive_odds_heavy_underdog(self):
        """Test break-even probability for heavy underdog (+250)."""
        prob = american_odds_to_break_even_probability(250)
        assert pytest.approx(prob, 0.01) == 0.286
    
    def test_even_money(self):
        """Test break-even probability for even money (-100)."""
        prob = american_odds_to_break_even_probability(-100)
        assert pytest.approx(prob, 0.01) == 0.5
    
    def test_none_odds(self):
        """Test handling of None odds."""
        prob = american_odds_to_break_even_probability(None)
        assert prob is None
    
    def test_zero_odds(self):
        """Test handling of zero odds."""
        prob = american_odds_to_break_even_probability(0)
        assert prob is None


class TestImputeMissingPercentage:
    """Test percentage imputation logic."""
    
    def test_impute_second_percentage(self):
        """Test imputing second percentage from first."""
        pct1, pct2 = impute_missing_percentage(65.0, None)
        assert pct1 == 65.0
        assert pct2 == 35.0
    
    def test_impute_first_percentage(self):
        """Test imputing first percentage from second."""
        pct1, pct2 = impute_missing_percentage(None, 35.0)
        assert pct1 == 65.0
        assert pct2 == 35.0
    
    def test_both_present(self):
        """Test when both percentages are present."""
        pct1, pct2 = impute_missing_percentage(60.0, 40.0)
        assert pct1 == 60.0
        assert pct2 == 40.0
    
    def test_both_missing(self):
        """Test when both percentages are missing."""
        pct1, pct2 = impute_missing_percentage(None, None)
        assert pct1 is None
        assert pct2 is None
    
    def test_impute_zero_percentage(self):
        """Test imputing from zero percentage."""
        pct1, pct2 = impute_missing_percentage(0.0, None)
        assert pct1 == 0.0
        assert pct2 == 100.0


class TestMoneyMinusBetsScreen:
    """Test Money % minus Bets % screening system."""
    
    def test_money_greater_than_bets(self):
        """Test when money % is greater than bets %."""
        diff, passes = calculate_money_minus_bets_screen(65.0, 50.0)
        assert diff == 15.0
        assert passes is True
    
    def test_bets_greater_than_money(self):
        """Test when bets % is greater than money %."""
        diff, passes = calculate_money_minus_bets_screen(40.0, 60.0)
        assert diff == -20.0
        assert passes is True
    
    def test_equal_percentages(self):
        """Test when percentages are equal."""
        diff, passes = calculate_money_minus_bets_screen(50.0, 50.0)
        assert diff == 0.0
        assert passes is False
    
    def test_with_ticket_share_limit(self):
        """Test with maximum ticket share filter."""
        # Should pass: diff exists and ticket share is under limit
        diff, passes = calculate_money_minus_bets_screen(65.0, 45.0, max_ticket_share=70.0)
        assert diff == 20.0
        assert passes is True
    
    def test_with_ticket_share_limit_exceeded(self):
        """Test with ticket share exceeding limit."""
        # Should fail: Bets %, rather than Money %, exceeds the ticket limit.
        diff, passes = calculate_money_minus_bets_screen(65.0, 80.0, max_ticket_share=70.0)
        assert diff == -15.0
        assert passes is False

    def test_money_share_can_exceed_ticket_share_limit(self):
        """A large money share is the signal, not a reason to reject the row."""
        diff, passes = calculate_money_minus_bets_screen(80.0, 60.0, max_ticket_share=70.0)
        assert diff == 20.0
        assert passes is True
    
    def test_missing_money_percentage(self):
        """Test handling of missing money percentage."""
        diff, passes = calculate_money_minus_bets_screen(None, 50.0)
        assert diff is None
        assert passes is False
    
    def test_missing_bets_percentage(self):
        """Test handling of missing bets percentage."""
        diff, passes = calculate_money_minus_bets_screen(50.0, None)
        assert diff is None
        assert passes is False


class TestCompareLines:
    """Test line movement comparison logic."""
    
    def test_better_away_line(self):
        """Test when away gets better line."""
        current = (-5.0, 5.0)
        consensus = (-6.0, 6.0)
        label, movement = compare_lines(current, consensus, 'away')
        assert "Better for Away" in label
        assert movement == 1.0
    
    def test_worse_away_line(self):
        """Test when away gets worse line."""
        current = (-7.0, 7.0)
        consensus = (-6.0, 6.0)
        label, movement = compare_lines(current, consensus, 'away')
        assert "Worse for Away" in label
        assert movement == -1.0
    
    def test_better_home_line(self):
        """Test when home gets better line."""
        current = (-5.0, 5.0)
        consensus = (-6.0, 6.0)
        label, movement = compare_lines(current, consensus, 'home')
        assert "Worse for Home" in label
        assert movement == -1.0
    
    def test_worse_home_line(self):
        """Test when home gets worse line."""
        current = (-7.0, 7.0)
        consensus = (-6.0, 6.0)
        label, movement = compare_lines(current, consensus, 'home')
        assert "Better for Home" in label
        assert movement == 1.0
    
    def test_no_material_movement_away(self):
        """Test no material movement for away."""
        current = (-5.0, 5.0)
        consensus = (-5.1, 5.1)
        label, movement = compare_lines(current, consensus, 'away')
        assert "No Material Movement" in label
        assert pytest.approx(movement, 0.01) == 0.1
    
    def test_no_material_movement_home(self):
        """Test no material movement for home."""
        current = (-5.0, 5.0)
        consensus = (-4.9, 4.9)
        label, movement = compare_lines(current, consensus, 'home')
        assert "No Material Movement" in label
        assert pytest.approx(movement, 0.01) == 0.1
    
    def test_missing_current_line(self):
        """Test with missing current line."""
        label, movement = compare_lines(None, (-6.0, 6.0), 'away')
        assert label == "N/A"
        assert movement == 0.0
    
    def test_missing_consensus_line(self):
        """Test with missing consensus line."""
        label, movement = compare_lines((-5.0, 5.0), None, 'away')
        assert label == "N/A"
        assert movement == 0.0


class TestValidatePercentages:
    """Test percentage validation logic."""
    
    def test_valid_percentages(self):
        """Test validation of valid percentages."""
        flags = validate_percentages(50.0, 50.0)
        assert not flags.invalid_percentage
        assert not flags.missing_percentage
    
    def test_valid_first_only(self):
        """Test validation with only first percentage."""
        flags = validate_percentages(65.0, None)
        assert not flags.invalid_percentage
    
    def test_out_of_range_high(self):
        """Test validation of percentage over 100."""
        flags = validate_percentages(150.0, None)
        assert flags.invalid_percentage
    
    def test_out_of_range_low(self):
        """Test validation of negative percentage."""
        flags = validate_percentages(-10.0, None)
        assert flags.invalid_percentage
    
    def test_both_missing(self):
        """Test validation when both missing."""
        flags = validate_percentages(None, None)
        assert flags.missing_percentage
    
    def test_zero_percentage_valid(self):
        """Test that zero percentage is valid."""
        flags = validate_percentages(0.0, 100.0)
        assert not flags.invalid_percentage
        assert not flags.missing_percentage


class TestDataQualityFlags:
    """Test DataQualityFlags utility class."""
    
    def test_has_issues_false(self):
        """Test has_issues returns False when no flags set."""
        flags = DataQualityFlags()
        assert not flags.has_issues()
    
    def test_has_issues_true_malformed(self):
        """Test has_issues returns True when malformed flag set."""
        flags = DataQualityFlags(malformed_team_code=True)
        assert flags.has_issues()

    def test_has_issues_true_missing_spread(self):
        flags = DataQualityFlags(missing_spread=True)
        assert flags.has_issues()

    def test_has_issues_true_invalid_odds(self):
        flags = DataQualityFlags(invalid_odds=True)
        assert flags.has_issues()


@pytest.mark.parametrize("code", ["UTSA", "TXST", "MSST", "MINN", "SJSU", "PITT", "M-OH"])
def test_supported_scoresandodds_team_codes(code):
    parsed, flags = parse_team_code(code)
    assert parsed == code
    assert not flags.malformed_team_code


def test_bettor_favorable_spread_movement():
    label, movement = compare_lines((-4.5, 4.5), (-5.5, 5.5), "away")
    assert label == "Better for Away"
    assert movement == 1.0


def test_bettor_favorable_total_movement_for_over():
    label, movement = compare_total_lines(45.5, 47.0, "over")
    assert label == "Better for Over"
    assert movement == -1.5


def test_bettor_favorable_total_movement_for_under():
    label, movement = compare_total_lines(47.0, 45.5, "under")
    assert label == "Better for Under"
    assert movement == 1.5
