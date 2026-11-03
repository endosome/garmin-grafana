"""Which tests assert the values each dashboard panel shows, or why none does.

Every panel of every dashboard must be listed, as 'File.json:panel id'. A new panel therefore cannot land without either a
value test (name it here) or a written reason. Chart panels that only a screenshot baseline covers are in `SCREENSHOT`. The terminal summary prints the share of panels with value tests.
"""

# panel -> tests that assert its values
VALUE_TESTS = {
    'Garmin-Grafana-Dashboard.json:38': ['test_personas.py::test_recent_activity_table_lists_the_last_weeks_activities'],
    'Garmin-Ski-Season.json:3': ['test_personas.py::test_ski_vertical_per_week_tile_averages_the_last_four_weeks_on_foot'],
    'Garmin-Strength-Training-Dashboard.json:1014': ['test_personas.py::test_recent_sets_table_lists_the_latest_weighted_sets_first'],
    'Garmin-Grafana-Dashboard.json:1': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone'],  # Daily Steps
    'Garmin-Grafana-Dashboard.json:15': ['test_personas.py::test_dashboards_show_a_real_fitbit_month'],  # Heart Rate
    'Garmin-Grafana-Dashboard.json:26': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Last Sync 
    'Garmin-Grafana-Dashboard.json:28': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Today RHR
    'Garmin-Grafana-Dashboard.json:29': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Stress
    'Garmin-Grafana-Dashboard.json:30': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Today's Steps
    'Garmin-Grafana-Dashboard.json:31': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Today SpO2
    'Garmin-Grafana-Dashboard.json:33': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # High Stress Duration
    'Garmin-Grafana-Dashboard.json:34': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person'],  # Weight
    'Garmin-Grafana-Dashboard.json:45': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # GPS Track Heart Rate
    'Garmin-Grafana-Dashboard.json:59': ['test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person'],  # Week at a Glance
    'Garmin-Grafana-Dashboard.json:60': ['test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone'],  # Battery Level (Requires GarminHomeAssistant from ConnectIQ integration)
    'Garmin-Grafana-Dashboard.json:67': ['test_personas.py::test_dashboards_show_a_real_fitbit_month'],  # Month at a Glance (Intraday)
    'Garmin-Grafana-Dashboard.json:68': ['test_personas.py::test_hourly_steps_add_up_on_the_25_hour_dst_day'],  # Steps Hourly (24h)
    'Garmin-Grafana-Dashboard.json:71': ['test_storage.py::test_record_fetch_stamps_the_source_for_the_last_fetch_panel'],  # Last Fetch
    'Garmin-Health-Overview.json:1': ['test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Sleep score
    'Garmin-Health-Overview.json:13': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet'],  # Overnight HRV
    'Garmin-Health-Overview.json:14': ['test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone'],  # Resting heart rate
    'Garmin-Health-Overview.json:18': ['test_personas.py::test_dashboards_show_a_real_fitbit_month'],  # Sleep per night
    'Garmin-Health-Overview.json:19': ['test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Sleep score
    'Garmin-Health-Overview.json:2': ['test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced', 'test_storage.py::test_rows_with_one_key_in_a_batch_merge_like_consecutive_upserts', 'test_storage.py::test_unchanged_rows_are_not_rewritten'],  # Sleep
    'Garmin-Health-Overview.json:25': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person', 'test_personas.py::test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone'],  # Steps
    'Garmin-Health-Overview.json:27': ['test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone'],  # Training time per week by sport
    'Garmin-Health-Overview.json:28': ['test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person'],  # Activities in the selected period
    'Garmin-Health-Overview.json:29': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # VO2 max
    'Garmin-Health-Overview.json:3': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # HRV
    'Garmin-Health-Overview.json:31': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person'],  # Weight
    'Garmin-Health-Overview.json:36': ['test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person'],  # Lifestyle journal
    'Garmin-Health-Overview.json:4': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Resting HR
    'Garmin-Health-Overview.json:42': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # VO2 max
    'Garmin-Health-Overview.json:43': ['test_personas.py::test_dashboards_show_a_real_fitbit_month'],  # Sleep stages per night (stage timeline)
    'Garmin-Health-Overview.json:5': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Battery at wake
    'Garmin-Health-Overview.json:6': ['test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Stress today
    'Garmin-Health-Overview.json:7': ['test_personas.py::test_garmin_health_panels_match_the_healthy_lifestyle_person', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Steps today
    'Garmin-Health-Overview.json:8': ['test_personas.py::test_dashboards_show_a_real_fitbit_month', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # Intensity, 7 days
    'Garmin-Health-Overview.json:9': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet'],  # Recovery compared with your usual
    'Garmin-Ski-Season.json:1': ['test_personas.py::test_ski_season_countdown_and_season_day', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Ski season
    'Garmin-Ski-Season.json:10': ['test_personas.py::test_ski_season_panels_match_the_ski_tourer', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Vertical on foot per week
    'Garmin-Ski-Season.json:12': ['test_personas.py::test_ski_season_panels_match_the_ski_tourer', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Climbing rate on uphill sessions
    'Garmin-Ski-Season.json:15': ['test_personas.py::test_ski_season_panels_match_the_ski_tourer'],  # Strength sets per week by body area
    'Garmin-Ski-Season.json:17': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet'],  # Overnight HRV
    'Garmin-Ski-Season.json:19': ['test_personas.py::test_ski_season_panels_match_the_ski_tourer', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Season on snow
    'Garmin-Ski-Season.json:2': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Vertical, 7 days
    'Garmin-Ski-Season.json:20': ['test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Snow sessions
    'Garmin-Ski-Season.json:4': ['test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Uphill time, 7 days
    'Garmin-Ski-Season.json:5': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet', 'test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Longest uphill, 4 weeks
    'Garmin-Ski-Season.json:6': ['test_storage.py::test_ski_season_groups_sports_and_counts_vertical_on_foot'],  # Strength, 14 days
    'Garmin-Ski-Season.json:7': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet'],  # Load ramp
    'Garmin-Ski-Season.json:8': ['test_personas.py::test_new_users_get_no_usual_range_or_load_yet', 'test_personas.py::test_records_without_local_dates_use_the_dashboard_time_zone', 'test_storage.py::test_health_today_tiles_stay_empty_until_today_is_synced'],  # VO2 max
    'Garmin-Strength-Training-Dashboard.json:1002': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Total Volume
    'Garmin-Strength-Training-Dashboard.json:1003': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Weighted Sets
    'Garmin-Strength-Training-Dashboard.json:1004': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Total Reps
    'Garmin-Strength-Training-Dashboard.json:1005': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Number of Workouts
    'Garmin-Strength-Training-Dashboard.json:1006': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Avg Volume / Workout
    'Garmin-Strength-Training-Dashboard.json:1008': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Estimated 1RM Progression (Epley) by Exercise
    'Garmin-Strength-Training-Dashboard.json:1010': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Personal Records (Top Set & Est. 1RM)
    'Garmin-Strength-Training-Dashboard.json:1013': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Sets by Category
    'Garmin-Strength-Training-Dashboard.json:1015': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Workout Summary (per session)
    'Garmin-Strength-Training-Dashboard.json:1017': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # HR Zone Time Distribution (Strength Sessions)
    'Garmin-Strength-Training-Dashboard.json:1020': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Workouts per Week
    'Garmin-Strength-Training-Dashboard.json:1022': ['test_personas.py::test_strength_dashboard_matches_the_gym_goer'],  # Sets Needing Classification (UNKNOWN)
}

# panel -> why no test asserts its values
NO_VALUES = {
    'Garmin-Grafana-Dashboard.json:49': 'geomap: the basemap comes from the internet, which the browser tests block; the query is covered by the contract and persona tests',
    'Garmin-Grafana-Dashboard.json:50': 'geomap: the basemap comes from the internet, which the browser tests block; the query is covered by the contract and persona tests',
    'Garmin-Grafana-Dashboard.json:69': 'geomap: the basemap comes from the internet, which the browser tests block; the query is covered by the contract and persona tests',
    'Garmin-Grafana-Dashboard.json:25': 'text panel: static content',
    'Garmin-Grafana-Dashboard.json:70': 'button: the insert is checked by test_fetch_now_button_inserts_through_its_own_datasource_outside_the_kiosk',
    'Garmin-Health-Overview.json:41': 'the version of the application: not a health value',
}

# Chart panels whose written reason for having no value test is that a reviewed screenshot baseline asserts what they draw (e2e/tests/__screenshots__/screenshots.spec.ts/),
# taken on one persona over one range. A change to what the panel draws is a diff someone has to accept.
SCREENSHOT = {
    'Garmin-Grafana-Dashboard.json:10': 'stats-10.png',
    'Garmin-Grafana-Dashboard.json:11': 'stats-11.png',
    'Garmin-Grafana-Dashboard.json:12': 'stats-12.png',
    'Garmin-Grafana-Dashboard.json:13': 'stats-13.png',
    'Garmin-Grafana-Dashboard.json:16': 'stats-16.png',
    'Garmin-Grafana-Dashboard.json:17': 'stats-17.png',
    'Garmin-Grafana-Dashboard.json:18': 'stats-18.png',
    'Garmin-Grafana-Dashboard.json:19': 'stats-19.png',
    'Garmin-Grafana-Dashboard.json:20': 'stats-20.png',
    'Garmin-Grafana-Dashboard.json:21': 'stats-21.png',
    'Garmin-Grafana-Dashboard.json:22': 'stats-22.png',
    'Garmin-Grafana-Dashboard.json:23': 'stats-23.png',
    'Garmin-Grafana-Dashboard.json:27': 'stats-27.png',
    'Garmin-Grafana-Dashboard.json:3': 'stats-3.png',
    'Garmin-Grafana-Dashboard.json:32': 'stats-32.png',
    'Garmin-Grafana-Dashboard.json:35': 'stats-35.png',
    'Garmin-Grafana-Dashboard.json:36': 'stats-36.png',
    'Garmin-Grafana-Dashboard.json:37': 'stats-37.png',
    'Garmin-Grafana-Dashboard.json:4': 'stats-4.png',
    'Garmin-Grafana-Dashboard.json:41': 'stats-41.png',
    'Garmin-Grafana-Dashboard.json:43': 'stats-43.png',
    'Garmin-Grafana-Dashboard.json:44': 'stats-44.png',
    'Garmin-Grafana-Dashboard.json:46': 'stats-46.png',
    'Garmin-Grafana-Dashboard.json:47': 'stats-47.png',
    'Garmin-Grafana-Dashboard.json:48': 'stats-48.png',
    'Garmin-Grafana-Dashboard.json:51': 'stats-51.png',
    'Garmin-Grafana-Dashboard.json:52': 'stats-52.png',
    'Garmin-Grafana-Dashboard.json:53': 'stats-53.png',
    'Garmin-Grafana-Dashboard.json:54': 'stats-54.png',
    'Garmin-Grafana-Dashboard.json:55': 'stats-55.png',
    'Garmin-Grafana-Dashboard.json:57': 'stats-57.png',
    'Garmin-Grafana-Dashboard.json:58': 'stats-58.png',
    'Garmin-Grafana-Dashboard.json:6': 'stats-6.png',
    'Garmin-Grafana-Dashboard.json:61': 'stats-61.png',
    'Garmin-Grafana-Dashboard.json:62': 'stats-62.png',
    'Garmin-Grafana-Dashboard.json:63': 'stats-63.png',
    'Garmin-Grafana-Dashboard.json:64': 'stats-64.png',
    'Garmin-Grafana-Dashboard.json:65': 'stats-65.png',
    'Garmin-Grafana-Dashboard.json:66': 'stats-66.png',
    'Garmin-Grafana-Dashboard.json:7': 'stats-7.png',
    'Garmin-Health-Overview.json:10': 'health-10.png',
    'Garmin-Health-Overview.json:11': 'health-11.png',
    'Garmin-Health-Overview.json:15': 'health-15.png',
    'Garmin-Health-Overview.json:16': 'health-16.png',
    'Garmin-Health-Overview.json:21': 'health-21.png',
    'Garmin-Health-Overview.json:22': 'health-22.png',
    'Garmin-Health-Overview.json:23': 'health-23.png',
    'Garmin-Health-Overview.json:26': 'health-26.png',
    'Garmin-Health-Overview.json:30': 'health-30.png',
    'Garmin-Health-Overview.json:33': 'health-33.png',
    'Garmin-Health-Overview.json:34': 'health-34.png',
    'Garmin-Health-Overview.json:38': 'health-38.png',
    'Garmin-Health-Overview.json:39': 'health-39.png',
    'Garmin-Ski-Season.json:11': 'ski-11.png',
    'Garmin-Ski-Season.json:13': 'ski-13.png',
    'Garmin-Ski-Season.json:16': 'ski-16.png',
    'Garmin-Ski-Season.json:21': 'ski-21.png',
    'Garmin-Ski-Season.json:23': 'ski-23.png',
    'Garmin-Ski-Season.json:24': 'ski-24.png',
    'Garmin-Strength-Training-Dashboard.json:1009': 'strength-1009.png',
    'Garmin-Strength-Training-Dashboard.json:1012': 'strength-1012.png',
    'Garmin-Strength-Training-Dashboard.json:1018': 'strength-1018.png',
    'Garmin-Strength-Training-Dashboard.json:1021': 'strength-1021.png',
}
