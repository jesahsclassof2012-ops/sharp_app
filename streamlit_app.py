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

# Define baseline handle values
baseline_handles = {
    "NFL": 12_000_000,
    "NCAAF": 4_000_000,
    "NBA": 2_000_000,
    "MLB": 1_000_000,
    "NHL": 800_000,
    "Others": 500_000
}

# Define scaling factor
scaling_factor = 0.000001

# Function to determine decision logic label based on Actual Diff %
def get_decision_label(actual_diff):
    if actual_diff >= 15:
        return '🔒 Sharp Money Play'
    elif actual_diff <= -15:
        return '🚫 Public Trap (Fade)'
    elif actual_diff > -10 and actual_diff < 10:
        return '🤷‍♂️ No Signal'
    else:
        return 'Neutral'

# Function to determine Confidence Score Label based on Confidence Score ranges
def get_confidence_score_label(confidence_score):
    if confidence_score >= 10 and confidence_score <= 20:
        return '🔒 Verified Sharp Play'
    elif confidence_score >= 5 and confidence_score < 10:
        return '⚙️ Lean Sharp / Monitor'
    elif confidence_score > -5 and confidence_score < 5:
        return '🤷‍♂️ No Signal / Neutral'
    elif confidence_score >= -10 and confidence_score <= -5:
        return '⚠️ Public-lean bias'
    elif confidence_score < -10:
        return '🚫 Public Trap (Fade)'
    else:
        return 'Other' # Handle any cases outside the defined ranges


def fetch_and_process_data(sport):
    """Fetches and processes consensus pick data for a given sport."""
    st.write(f"Fetching data for {sport}...")
    url = f"https://www.scoresandodds.com/{sport.lower()}/consensus-picks"
    st.write(f"Fetching URL: {url}")

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/555.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/555.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image:*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        html_content_new = response.text
        st.write(f"Successfully fetched page content for {sport}.")

        if "There are no games scheduled today." in html_content_new:
            st.write("There were no games scheduled today.")
            return pd.DataFrame()

        soup = BeautifulSoup(html_content_new, 'html.parser')
        matchup_containers = soup.find_all('div', class_='trend-card')

        data_new = []

        for container in matchup_containers:
            chart = container.find('span', class_='trend-graph-chart')
            odds_element = container.find('span', class_='best-odds')
            localtime_element = container.find('span', attrs={"data-role": "localtime"})

            if not chart:
                continue

            away_odds = None
            home_odds = None
            if odds_element:
                odds_containers_inner = odds_element.find_all('div', class_='best-odds-container')
                for inner_container in odds_containers_inner:
                     span_text = inner_container.find('span').get_text(strip=True)
                     if 'Best away Odds' in span_text:
                         away_other_odds = inner_container.find('small', class_='data-odds best')
                         if away_other_odds:
                             away_odds = away_other_odds.get_text(strip=True)
                         else:
                             away_moneyline_odds = inner_container.find('span', class_='data-moneyline')
                             if away_moneyline_odds:
                                 away_odds = away_moneyline_odds.get_text(strip=True)

                     elif 'Best home Odds' in span_text:
                          home_other_odds = inner_container.find('small', class_='data-odds best')
                          if home_other_odds:
                              home_odds = home_other_odds.get_text(strip=True)
                          else:
                              home_moneyline_odds = inner_container.find('span', class_='data-moneyline')
                              if home_moneyline_odds:
                                  home_odds = home_moneyline_odds.get_text(strip=True)
            current_odds = {'away_odds': away_odds, 'home_odds': home_odds}

            current_localtime = 'N/A'
            if localtime_element:
                localtime_value = localtime_element.get('data-value')
                if localtime_value:
                    try:
                        pst = pytz.timezone('America/Los_Angeles')
                        utc_time = datetime.fromisoformat(localtime_value.replace('Z', '+00:00'))
                        pst_time = utc_time.astimezone(pst)
                        current_localtime = pst_time.strftime('%m/%d %I:%M%p').replace('AM', 'am').replace('PM', 'pm')
                    except ValueError:
                        pass

            sides_element_bets = chart.find('span', class_='trend-graph-sides')
            teams = []
            betting_label_bets = 'N/A'
            if sides_element_bets:
                teams = [team.get_text(strip=True).replace('\n', '') for team in sides_element_bets.find_all('strong')]
                betting_label_bets = sides_element_bets.find('span').get_text(strip=True) if sides_element_bets.find('span') else 'N/A'

            percentages_bets_element = chart.find_all('span', class_='trend-graph-percentage')
            bets_percentage_pair = {}
            if percentages_bets_element:
                bets_spans = percentages_bets_element[0].find_all('span')
                if len(bets_spans) >= 2:
                    bets_percentage_pair = {
                        'team1_percentage': extract_percentage(bets_spans[0]),
                        'team2_percentage': extract_percentage(bets_spans[1])
                    }

            money_percentage_pair = {}
            if len(percentages_bets_element) > 1:
                money_spans = percentages_bets_element[1].find_all('span')
                if len(money_spans) >= 2:
                    money_percentage_pair = {
                        'team1_percentage': extract_percentage(money_spans[0]),
                        'team2_percentage': extract_percentage(money_spans[1])
                    }

            sides_element_money = chart.find('span', class_='trend-graph-sides center')
            betting_label_money = 'N/A'
            if sides_element_money:
                betting_label_money = sides_element_money.find('span').get_text(strip=True) if sides_element_money.find('span') else 'N/A'

            if teams:
                entry_data = {
                    'teams': teams,
                    'betting_label_bets': betting_label_bets,
                    'bets_percentages': bets_percentage_pair,
                    'betting_label_money': betting_label_money,
                    'money_percentages': money_percentage_pair,
                    'best_odds': current_odds,
                    'matchup_time': current_localtime
                }
                data_new.append(entry_data)


        moneyline_data = {}
        spread_data = {}
        total_data = {}
        current_matchup_teams = (None, None)
        current_odds = None
        current_matchup_time = 'N/A'

        for entry in data_new:
            teams = entry.get('teams', [])
            betting_label_bets = entry.get('betting_label_bets', 'N/A')
            bets_percentages = entry.get('bets_percentages', {})
            betting_label_money = entry.get('betting_label_money', 'N/A')
            money_percentages = entry.get('money_percentages', {})
            entry_odds = entry.get('best_odds')
            entry_matchup_time = entry.get('matchup_time', 'N/A')

            betting_category = 'Unknown'
            total_line = None
            spread_line = None

            if betting_label_bets == '% of Bets':
                if len(teams) >= 2:
                     team1_name_raw = teams[0]
                     team2_name_raw = teams[1]

                     if re.match(r'^[A-Z]{2,3}$', team1_name_raw) and re.match(r'^[A-Z]{2,3}$', team2_name_raw):
                         betting_category = 'Moneyline'
                         current_matchup_teams = (team1_name_raw, team2_name_raw)
                         current_odds = entry_odds
                         current_matchup_time = entry_matchup_time
                     elif re.search(r'[\+\-]', team1_name_raw) or re.search(r'[\+\-]', team2_name_raw):
                         betting_category = 'Spread'
                         team1_name = re.findall(r'^[A-Z]{2,3}', team1_name_raw)[0] if re.findall(r'^[A-Z]{2,3}', team1_name_raw) else team1_name_raw
                         team2_name = re.findall(r'^[A-Z]{2,3}', team2_name_raw)[0] if re.findall(r'^[A-Z]{2,3}', team2_name_raw) else team2_name_raw
                         current_matchup_teams = (team1_name, team2_name)
                         current_odds = entry_odds
                         current_matchup_time = entry_matchup_time

                         spread_line_match1 = re.search(r'([\+\-]?[\d+\.]+)', team1_name_raw)
                         spread_line_match2 = re.search(r'([\+\-]?[\d+\.]+)', team2_name_raw)
                         if spread_line_match1 and spread_line_match2:
                             spread_line = f"{spread_line_match1.group(1)} / {spread_line_match2.group(1)}"

            elif '(' in betting_label_bets and ')' in betting_label_bets and ('o' in betting_label_bets or 'u' in betting_label_bets):
                betting_category = 'Total'
                if len(teams) >= 2:
                    line_match = re.search(r'\(?[ou]([\d+\.]+)\)?', teams[0])
                    if line_match:
                        total_line = line_match.group(1)
                current_odds = entry_odds
                current_matchup_time = entry_matchup_time


            if current_matchup_teams[0] and current_matchup_teams[1]:
                matchup_key = f"{current_matchup_teams[0]} vs {current_matchup_teams[1]}"

                team1_bets_percentage = bets_percentages.get('team1_percentage', 'N/A')
                team2_bets_percentage = bets_percentages.get('team2_percentage', 'N/A')
                team1_money_percentage = money_percentages.get('team1_percentage', 'N/A')
                team2_money_percentage = money_percentages.get('team2_percentage', 'N/A')

                if betting_category == 'Moneyline':
                    if matchup_key not in moneyline_data:
                        moneyline_data[matchup_key] = {'Matchup Teams': matchup_key, 'Away Odds': entry_odds['away_odds'], 'Home Odds': entry_odds['home_odds'], 'Matchup Time': current_matchup_time}
                    moneyline_data[matchup_key]['Team 1 Bets %'] = team1_bets_percentage
                    moneyline_data[matchup_key]['Team 2 Bets %'] = team2_bets_percentage
                    moneyline_data[matchup_key]['Team 1 Money %'] = team1_money_percentage
                    moneyline_data[matchup_key]['Team 2 Money %'] = team2_money_percentage
                elif betting_category == 'Spread':
                    if matchup_key not in spread_data:
                        spread_data[matchup_key] = {'Matchup Teams': matchup_key, 'Spread Line': 'N/A', 'Away Odds': entry_odds['away_odds'], 'Home Odds': entry_odds['home_odds'], 'Matchup Time': current_matchup_time}
                    spread_data[matchup_key]['Team 1 Bets %'] = team1_bets_percentage
                    spread_data[matchup_key]['Team 2 Bets %'] = team2_bets_percentage
                    spread_data[matchup_key]['Team 1 Money %'] = team1_money_percentage
                    spread_data[matchup_key]['Team 2 Money %'] = team2_money_percentage
                    spread_data[matchup_key]['Spread Line'] = spread_line
                elif betting_category == 'Total':
                     if matchup_key not in total_data:
                         total_data[matchup_key] = {'Matchup Teams': matchup_key, 'Total Line': 'N/A', 'Away Odds': entry_odds['away_odds'], 'Home Odds': entry_odds['home_odds'], 'Matchup Time': current_matchup_time}
                     total_data[matchup_key]['Over Bets %'] = team1_bets_percentage
                     total_data[matchup_key]['Under Bets %'] = team2_bets_percentage
                     total_data[matchup_key]['Over Money %'] = team1_money_percentage
                     total_data[matchup_key]['Under Money %'] = team2_money_percentage
                     total_data[matchup_key]['Total Line'] = total_line


        moneyline_list = list(moneyline_data.values())
        spread_list = list(spread_data.values())
        total_list = list(total_data.values())

        df_moneyline = pd.DataFrame(moneyline_list)
        df_spread = pd.DataFrame(spread_list)
        df_total = pd.DataFrame(total_list)


        def impute_percentage(df, col1, col2):
            for index, row in df.iterrows():
                p1 = row[col1]
                p2 = row[col2]
                if p1 is not None and p2 is None:
                     df.at[index, col2] = 100.0 - p1
                elif p2 is not None and p1 is None:
                     df.at[index, col1] = 100.0 - p2

        impute_percentage(df_moneyline, 'Team 1 Bets %', 'Team 2 Bets %')
        impute_percentage(df_moneyline, 'Team 1 Money %', 'Team 2 Money %')
        impute_percentage(df_spread, 'Team 1 Bets %', 'Team 2 Bets %')
        impute_percentage(df_spread, 'Team 1 Money %', 'Team 2 Money %')
        impute_percentage(df_total, 'Over Bets %', 'Under Bets %')
        impute_percentage(df_total, 'Over Money %', 'Under Money %')

        for df in [df_moneyline, df_spread, df_total]:
            for col in df.columns:
                if '%' in col:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

        qualified_picks = []
        required_diff = 0


        if not df_moneyline.empty:
            for index, row in df_moneyline.iterrows():
                matchup = row['Matchup Teams']
                team1_name, team2_name = matchup.split(" vs ")
                away_odds = row.get('Away Odds', 'N/A')
                home_odds = row.get('Home Odds', 'N/A')
                matchup_time = row.get('Matchup Time', 'N/A')

                team1_bets = row.get('Team 1 Bets %')
                team1_money = row.get('Team 1 Money %')
                if team1_bets is not None and team1_money is not None:
                    qualified_picks.append({
                        'Matchup': matchup,
                        'Team': team1_name,
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Moneyline',
                        'Bets %': team1_bets,
                        'Money %': team1_money,
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(team1_money - team1_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })

                team2_bets = row.get('Team 2 Bets %')
                team2_money = row.get('Team 2 Money %')
                if team2_bets is not None and team2_money is not None:
                     qualified_picks.append({
                        'Matchup': matchup,
                        'Team': team2_name,
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Moneyline',
                        'Bets %': team2_bets,
                        'Money %': team2_money,
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(team2_money - team2_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })

        if not df_spread.empty:
            for index, row in df_spread.iterrows():
                matchup = row['Matchup Teams']
                team1_name, team2_name = matchup.split(" vs ")
                away_odds = row.get('Away Odds', 'N/A')
                home_odds = row.get('Home Odds', 'N/A')
                matchup_time = row.get('Matchup Time', 'N/A')

                team1_bets = row.get('Team 1 Bets %')
                team1_money = row.get('Team 1 Money %')
                if team1_bets is not None and team1_money is not None:
                    qualified_picks.append({
                        'Matchup': matchup,
                        'Team': team1_name,
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Spread',
                        'Bets %': team1_bets,
                        'Money %': team1_money,
                        'Spread Line': row.get('Spread Line', 'N/A'),
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(team1_money - team1_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })

                team2_bets = row.get('Team 2 Bets %')
                team2_money = row.get('Team 2 Money %')
                if team2_bets is not None and team2_money is not None:
                     qualified_picks.append({
                        'Matchup': matchup,
                        'Team': team2_name,
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Spread',
                        'Bets %': team2_bets,
                        'Money %': team2_money,
                        'Spread Line': row.get('Spread Line', 'N/A'),
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(team2_money - team2_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })

        if not df_total.empty:
            for index, row in df_total.iterrows():
                matchup = row['Matchup Teams']
                team1_name, team2_name = matchup.split(" vs ")
                away_odds = row.get('Away Odds', 'N/A')
                home_odds = row.get('Home Odds', 'N/A')
                matchup_time = row.get('Matchup Time', 'N/A')

                over_bets = row.get('Over Bets %')
                over_money = row.get('Over Money %')
                if over_bets is not None and over_money is not None:
                    qualified_picks.append({
                        'Matchup': matchup,
                        'Team': f"Over {row.get('Total Line', 'N/A')}",
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Total',
                        'Bets %': over_bets,
                        'Money %': over_money,
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(over_money - over_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })

                under_bets = row.get('Under Bets %')
                under_money = row.get('Under Money %')
                if under_bets is not None and under_money is not None:
                    qualified_picks.append({
                        'Matchup': matchup,
                        'Team': f"Under {row.get('Total Line', 'N/A')}",
                        'Matchup Time': matchup_time,
                        'Betting Category': 'Total',
                        'Bets %': under_bets,
                        'Money %': under_money,
                        'Required Diff %': required_diff,
                        'Actual Diff %': round(under_money - under_bets, 2),
                        'Away Odds': away_odds,
                        'Home Odds': home_odds
                    })


        df_picks_meeting_thresholds = pd.DataFrame(qualified_picks)

        if 'Required Diff %' in df_picks_meeting_thresholds.columns:
            df_picks_meeting_thresholds = df_picks_meeting_thresholds.drop(columns=['Required Diff %'])

        df_picks_meeting_thresholds = df_picks_meeting_thresholds[(df_picks_meeting_thresholds['Actual Diff %'].abs() > 1)].copy()

        df_picks_meeting_thresholds['Sport'] = sport
        df_picks_meeting_thresholds['est_handle'] = df_picks_meeting_thresholds['Sport'].apply(lambda s: baseline_handles.get(s, 0) * scaling_factor)

        df_picks_meeting_thresholds['Divergence'] = abs(df_picks_meeting_thresholds['Bets %'] - df_picks_meeting_thresholds['Money %'])
        df_picks_meeting_thresholds['Disagreement Index'] = df_picks_meeting_thresholds[['Bets %', 'Money %']].min(axis=1)
        df_picks_meeting_thresholds['Consensus Strength'] = df_picks_meeting_thresholds[['Bets %', 'Money %']].max(axis=1)
        df_picks_meeting_thresholds['Weighted Signal'] = df_picks_meeting_thresholds['est_handle'] * df_picks_meeting_thresholds['Disagreement Index'] * df_picks_meeting_thresholds['Consensus Strength'] / 1_000_000
        df_picks_meeting_thresholds['Decision Logic'] = df_picks_meeting_thresholds['Actual Diff %'].apply(get_decision_label)
        df_picks_meeting_thresholds['Relative Differential'] = df_picks_meeting_thresholds.apply(
            lambda row: row['Actual Diff %'] * row['Bets %'] / 100 if row['Bets %'] is not None else None,
            axis=1
        )
        df_picks_meeting_thresholds = df_picks_meeting_thresholds[df_picks_meeting_thresholds['Relative Differential'].abs() >= 1].copy()
        df_picks_meeting_thresholds['Confidence Score'] = (0.45 * df_picks_meeting_thresholds['Relative Differential']) + \
                                                          (0.35 * df_picks_meeting_thresholds['Actual Diff %']) + \
                                                          (0.15 * df_picks_meeting_thresholds['Weighted Signal'] * 100) - \
                                                          (0.05 * df_picks_meeting_thresholds['Disagreement Index'])
        df_picks_meeting_thresholds['Confidence Score Label'] = df_picks_meeting_thresholds['Confidence Score'].apply(get_confidence_score_label)

        # Convert 'Matchup Time' to datetime objects with error handling and correct year
        df_picks_meeting_thresholds['Matchup Time'] = df_picks_meeting_thresholds['Matchup Time'].astype(str)
        # Get the current year to use for parsing
        current_year = datetime.now().year
        df_picks_meeting_thresholds['Matchup Time'] = df_picks_meeting_thresholds['Matchup Time'].apply(
            lambda x: datetime.strptime(f"{current_year}/{x}", '%Y/%m/%d %I:%M%p') if x != 'N/A' else None
        )

        # Check for any NaT values after conversion
        if df_picks_meeting_thresholds['Matchup Time'].isnull().any():
            st.warning("Some matchup times could not be parsed and may be excluded from time-based filtering.")

        # Localize the datetime objects to PST before comparison.
        pst = pytz.timezone('America/Los_Angeles')
        df_picks_meeting_thresholds['Matchup Time'] = df_picks_meeting_thresholds['Matchup Time'].apply(lambda x: pst.localize(x) if pd.notnull(x) else None)


        df_picks_meeting_thresholds = df_picks_meeting_thresholds.sort_values(by=['Matchup Time', 'Relative Differential'], ascending=[True, False])

        desired_column_order = ['Matchup', 'Team', 'Matchup Time', 'Betting Category', 'Decision Logic', 'Confidence Score Label', 'Relative Differential', 'Bets %', 'Money %', 'Actual Diff %', 'Away Odds', 'Home Odds', 'Spread Line', 'Sport']
        df_picks_meeting_thresholds = df_picks_meeting_thresholds.reindex(columns=desired_column_order)

        return df_picks_meeting_thresholds

    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching the page: {e}")
        return pd.DataFrame()

st.title("Sports Betting Consensus Picks")

sports = ["NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB"]
selected_sport = st.sidebar.selectbox("Select a Sport", sports)

# Add time window input to the sidebar with a default value of 1 hour
time_window_hours = st.sidebar.number_input(
    "Display games within the next (hours):",
    min_value=1,
    max_value=168, # Allow up to 7 days
    value=1,
    step=1,
    key='time_window_input'
)

# Add a state variable to trigger refresh
if 'refresh_data' not in st.session_state:
    st.session_state['refresh_data'] = False

# Check if refresh button in sidebar is clicked
if st.sidebar.button("Refresh Data (Sidebar)"):
    st.session_state['refresh_data'] = True


# Fetch data when the sport changes or the refresh state is True
if selected_sport and (st.session_state['refresh_data'] or 'df_picks' not in st.session_state or st.session_state['current_sport'] != selected_sport):
    with st.spinner(f"Refreshing data for {selected_sport}..."):
        df_picks = fetch_and_process_data(selected_sport)
        st.session_state['df_picks'] = df_picks
        st.session_state['current_sport'] = selected_sport
        st.session_state['refresh_data'] = False # Reset refresh state


# Access the dataframe from session state
df_picks = st.session_state.get('df_picks', pd.DataFrame())

# Get the current time in the appropriate timezone (America/Los_Angeles)
pst = pytz.timezone('America/Los_Angeles')
current_time_pst = datetime.now(pst)

# Calculate the end time for filtering
end_time_pst = current_time_pst + timedelta(hours=time_window_hours)

# Filter the DataFrame to include games within the selected time window
if not df_picks.empty:
    df_filtered_by_time = df_picks[
        (df_picks['Matchup Time'] >= current_time_pst) &
        (df_picks['Matchup Time'] <= end_time_pst)
    ].copy()
else:
    df_filtered_by_time = pd.DataFrame() # Ensure df_filtered_by_time is a DataFrame even if df_picks is empty


# Display data if available after filtering
if not df_filtered_by_time.empty:
    st.subheader(f"All Moneyline, Spread, and Total Picks for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    st.dataframe(df_filtered_by_time.style.hide(axis='index'))

    st.subheader(f"Moneyline Picks for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    df_moneyline_picks = df_filtered_by_time[df_filtered_by_time['Betting Category'] == 'Moneyline'].copy()
    if not df_moneyline_picks.empty:
        st.dataframe(df_moneyline_picks.style.hide(axis='index'))
    else:
        st.write(f"No Moneyline picks found meeting the filter criteria for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours.")

    st.subheader(f"Spread Picks for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    df_spread_picks = df_filtered_by_time[df_filtered_by_time['Betting Category'] == 'Spread'].copy()
    if not df_spread_picks.empty:
        st.dataframe(df_spread_picks.style.hide(axis='index'))
    else:
        st.write(f"No Spread picks found meeting the filter criteria for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours.")

    st.subheader(f"Total Picks for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    df_total_picks = df_filtered_by_time[df_filtered_by_time['Betting Category'] == 'Total'].copy()
    if not df_total_picks.empty:
        st.dataframe(df_total_picks.style.hide(axis='index'))
    else:
        st.write(f"No Total picks found meeting the filter criteria for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours.")


    st.subheader(f"Sharp Money Picks - Lean Sharp / Monitor Confidence for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    df_lean_sharp_picks = df_filtered_by_time[
        (df_filtered_by_time['Decision Logic'] == '🔒 Sharp Money Play') &
        (df_filtered_by_time['Confidence Score Label'] == '⚙️ Lean Sharp / Monitor')
    ].copy()
    if not df_lean_sharp_picks.empty:
        st.dataframe(df_lean_sharp_picks.style.hide(axis='index'))
    else:
        st.write(f"No Sharp Money picks found with Lean Sharp / Monitor confidence for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours.")

    st.subheader(f"Sharp Money Picks - Verified Sharp Play Confidence for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours")
    df_verified_sharp_picks = df_filtered_by_time[
        (df_filtered_by_time['Decision Logic'] == '🔒 Sharp Money Play') &
        (df_filtered_by_time['Confidence Score Label'] == '🔒 Verified Sharp Play')
    ].copy()
    if not df_verified_sharp_picks.empty:
        st.dataframe(df_verified_sharp_picks.style.hide(axis='index'))
    else:
        st.write(f"No Sharp Money picks found with Verified Sharp Play confidence for {st.session_state.get('current_sport', 'Selected Sport')} within the next {time_window_hours} hours.")
else:
    st.write(f"No data found for {st.session_state.get('current_sport', 'Selected Sport')} meeting the criteria within the next {time_window_hours} hours.")


# Check if refresh button at the bottom is clicked
main_page_refresh_button = st.button("Refresh Data")
if main_page_refresh_button:
    st.session_state['refresh_data'] = True
    st.rerun() # Use st.rerun() to trigger a rerun of the app to fetch new data
