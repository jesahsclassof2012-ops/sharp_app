import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from collections import defaultdict
import pandas as pd
from datetime import datetime, timezone, timedelta
import pytz

# Set page config
st.set_page_config(
    page_title="Sports Betting Consensus Picks",
    layout="wide",
    menu_items={
        'Get Help': 'https://www.scoresandodds.com/contact',
        'Report a bug': "https://github.com/streamlit/streamlit/issues",
        'About': "# This is a header. This is an *extremely* cool app!"
    },
    initial_sidebar_state="expanded"
)


# Define the dynamic threshold function
def get_dynamic_threshold(bets_percentage):
    """
    Calculates the required difference between Money % and Bets %
    based on a tiered dynamic threshold logic.
    """
    if pd.isna(bets_percentage):
        return 0 # Or some other appropriate default/indicator
    if bets_percentage <= 25:
        return 15
    elif bets_percentage <= 50:
        return 8
    elif bets_percentage <= 75:
        return 5
    else: # bets_percentage > 75
        return 3


# Function to extract percentage from text or style attribute
def extract_percentage(percentage_element):
    if percentage_element:
        text_percentage = percentage_element.get_text(strip=True).replace('%', '')
        if text_percentage and text_percentage != '&nbsp;':
            try:
                return float(text_percentage)
            except ValueError:
                pass  # Fallback to style if text is not a valid number

        # If text is not available or not a valid number, try to get from style attribute
        style = percentage_element.get('style')
        if style:
            width_match = re.search(r'width:\s*([\d+\.]+)\%', style)
            if width_match:
                try:
                    return float(width_match.group(1))
                except ValueError:
                    pass

    return None

# Function to extract and format betting lines from the best odds string
def extract_betting_lines(best_odds_string):
    if not best_odds_string or best_odds_string == 'N/A':
        return 'N/A'
    # Extract numbers with potential + or - signs
    lines = re.findall(r'[\+\-]?\d+', best_odds_string)
    if len(lines) >= 2:
        return f"{lines[0]} / {lines[1]}"
    elif len(lines) == 1:
        return lines[0]
    return 'N/A'

# Function to get confidence score label
def get_confidence_score_label(confidence_score):
    if pd.isna(confidence_score):
        return "N/A"
    elif confidence_score > 20:
        return "🔒 Verified Sharp Play"
    elif confidence_score >= 10:
        return "💎 Strong Sharp"
    elif confidence_score >= 5:
        return "📈 Medium Sharp"
    elif confidence_score > 0:
        return "📊 Slight Sharp"
    elif confidence_score == 0:
        return "⚖️ Neutral"
    elif confidence_score >= -5 and confidence_score < 0:
        return "⬇️ Slight Public"
    elif confidence_score >= -10 and confidence_score < -5:
        return "⚠️ Public-lean bias"
    elif confidence_score < -10:
        return "🚨 Strong Public"
    else:
        return "Other (Unhandled Score)"

# Define baseline handle values
baseline_handles = {
    "NFL": 12_000_000,
}
