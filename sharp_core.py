"""
Sharp Signal V2 Core Module
Pure parsing and math helpers for betting data analysis.
"""

import re
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass


@dataclass
class DataQualityFlags:
    """Flags indicating data quality issues."""
    malformed_team_code: bool = False
    missing_spread: bool = False
    missing_total: bool = False
    invalid_percentage: bool = False
    invalid_odds: bool = False
    missing_percentage: bool = False
    
    def has_issues(self) -> bool:
        """Returns True if any quality flags are set."""
        return any([
            self.malformed_team_code,
            self.missing_spread,
            self.missing_total,
            self.invalid_percentage,
            self.invalid_odds,
            self.missing_percentage
        ])


def parse_team_code(code: str) -> Tuple[Optional[str], DataQualityFlags]:
    """
    Parse team codes including 4-character codes (UTSA, TXST) and hyphenated codes (M-OH).
    
    Args:
        code: Raw team code string
        
    Returns:
        Tuple of (parsed_code, data_quality_flags)
    """
    flags = DataQualityFlags()
    
    if not code or not isinstance(code, str):
        flags.malformed_team_code = True
        return None, flags
    
    code = code.strip().upper()
    
    # Handle 2-3 character codes: AB, ABC
    if re.match(r'^[A-Z]{2,3}$', code):
        return code, flags
    
    # Handle 4-character codes: UTSA, TXST
    if re.match(r'^[A-Z]{4}$', code):
        return code, flags
    
    # Handle hyphenated codes: M-OH, etc.
    if re.match(r'^[A-Z]-[A-Z]{2}$', code):
        return code, flags
    
    flags.malformed_team_code = True
    return code, flags


def parse_spread(spread_str: str) -> Tuple[Optional[Tuple[float, float]], DataQualityFlags]:
    """
    Parse spread string like "-5.5 / +5.5" or "+110 / -110" (consensus line).
    
    Args:
        spread_str: Spread string from HTML
        
    Returns:
        Tuple of ((away_spread, home_spread), data_quality_flags)
    """
    flags = DataQualityFlags()
    
    if not spread_str or spread_str == 'N/A':
        flags.missing_spread = True
        return None, flags
    
    # Try to extract two numbers with +/- signs
    matches = re.findall(r'([\+\-]?\d+(?:\.\d+)?)', str(spread_str))
    
    if len(matches) >= 2:
        try:
            away = float(matches[0])
            home = float(matches[1])
            return (away, home), flags
        except ValueError:
            flags.invalid_spread = True
            return None, flags
    
    flags.missing_spread = True
    return None, flags


def parse_total(total_str: str) -> Tuple[Optional[float], DataQualityFlags]:
    """
    Parse total/over-under string like "o45.5" or "u45.5".
    
    Args:
        total_str: Total string from HTML
        
    Returns:
        Tuple of (total_line, data_quality_flags)
    """
    flags = DataQualityFlags()
    
    if not total_str or total_str == 'N/A':
        flags.missing_total = True
        return None, flags
    
    # Match o/u followed by number
    match = re.search(r'[ou](\d+(?:\.\d+)?)', str(total_str), re.IGNORECASE)
    
    if match:
        try:
            return float(match.group(1)), flags
        except ValueError:
            flags.invalid_total = True
            return None, flags
    
    flags.missing_total = True
    return None, flags


def parse_american_odds(odds_str: str) -> Tuple[Optional[int], DataQualityFlags]:
    """
    Parse American odds format like "-110", "+150", "110", "-110EV".
    
    Args:
        odds_str: Odds string from HTML
        
    Returns:
        Tuple of (american_odds, data_quality_flags)
    """
    flags = DataQualityFlags()
    
    if not odds_str or odds_str == 'N/A':
        flags.invalid_odds = True
        return None, flags
    
    # Extract number with optional +/- sign
    match = re.search(r'([\+\-]?\d+)', str(odds_str))
    
    if match:
        try:
            odds = int(match.group(1))
            # Validate American odds are reasonable
            if -10000 <= odds <= 10000 and odds != 0:
                return odds, flags
        except ValueError:
            pass
    
    flags.invalid_odds = True
    return None, flags


def american_odds_to_break_even_probability(odds: int) -> Optional[float]:
    """
    Convert American odds to break-even probability (before juice).
    
    Formula for negative odds (favorites): prob = abs(odds) / (abs(odds) + 100)
    Formula for positive odds (underdogs): prob = 100 / (odds + 100)
    
    Args:
        odds: American odds
        
    Returns:
        Break-even probability (0-1), or None if invalid
    """
    if not odds or odds == 0:
        return None
    
    if odds < 0:
        # Favorite: -110 -> 110/(110+100) = 0.524
        return abs(odds) / (abs(odds) + 100)
    else:
        # Underdog: +150 -> 100/(150+100) = 0.4
        return 100 / (odds + 100)


def impute_missing_percentage(pct1: Optional[float], pct2: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Impute a missing percentage from the other if one is available.
    Assumes they should sum to 100%.
    
    Args:
        pct1: First percentage (0-100) or None
        pct2: Second percentage (0-100) or None
        
    Returns:
        Tuple of (imputed_pct1, imputed_pct2)
    """
    if pct1 is not None and pct2 is None:
        return pct1, 100.0 - pct1
    elif pct2 is not None and pct1 is None:
        return 100.0 - pct2, pct2
    else:
        return pct1, pct2


def calculate_money_minus_bets_screen(
    money_pct: Optional[float], 
    bets_pct: Optional[float],
    max_ticket_share: Optional[float] = None
) -> Tuple[Optional[float], bool]:
    """
    Transparent Money % minus Bets % screening system.
    Replaces arbitrary Confidence Score and Relative Differential.
    
    Args:
        money_pct: Money percentage (0-100)
        bets_pct: Bets percentage (0-100)
        max_ticket_share: Optional maximum ticket share threshold (0-100)
        
    Returns:
        Tuple of (money_minus_bets, passes_filter)
    """
    if money_pct is None or bets_pct is None:
        return None, False
    
    diff = money_pct - bets_pct
    
    # Check ticket share (how concentrated on one side)
    ticket_share = max(money_pct, bets_pct)
    
    if max_ticket_share is not None:
        passes = abs(diff) > 0 and ticket_share <= max_ticket_share
    else:
        passes = abs(diff) > 0
    
    return diff, passes


def compare_lines(
    current_spread: Optional[Tuple[float, float]],
    consensus_spread: Optional[Tuple[float, float]],
    side: str = 'away'
) -> Tuple[str, float]:
    """
    Compare current best line to consensus split line.
    Determines if better or worse for bettors on a specific side.
    
    Args:
        current_spread: (away_spread, home_spread) current best line
        consensus_spread: (away_spread, home_spread) consensus line
        side: 'away' or 'home' - which side we're analyzing
        
    Returns:
        Tuple of (comparison_label, line_movement)
    """
    if not current_spread or not consensus_spread:
        return "N/A", 0.0
    
    side_idx = 0 if side == 'away' else 1
    
    current = current_spread[side_idx]
    consensus = consensus_spread[side_idx]
    movement = current - consensus
    
    if side == 'away':
        # Better if more positive (higher underdog odds or lower spread)
        if movement > 0.25:
            return "Better for Away", movement
        elif movement < -0.25:
            return "Worse for Away", movement
    else:
        # Better if more negative (lower home spread or higher underdog odds)
        if movement < -0.25:
            return "Better for Home", movement
        elif movement > 0.25:
            return "Worse for Home", movement
    
    return "No Material Movement", movement


def validate_percentages(pct1: Optional[float], pct2: Optional[float]) -> DataQualityFlags:
    """
    Validate that percentages are reasonable.
    
    Args:
        pct1: First percentage
        pct2: Second percentage
        
    Returns:
        Data quality flags with invalid_percentage set if issues found
    """
    flags = DataQualityFlags()
    
    if pct1 is None and pct2 is None:
        flags.missing_percentage = True
    elif pct1 is not None and (pct1 < 0 or pct1 > 100):
        flags.invalid_percentage = True
    elif pct2 is not None and (pct2 < 0 or pct2 > 100):
        flags.invalid_percentage = True
    
    return flags
