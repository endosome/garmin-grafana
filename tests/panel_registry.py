"""Which tests assert the values each dashboard panel shows, or why none does.

Every panel of every dashboard must be listed, as 'File.json:panel id'. A new panel therefore cannot land without either a
value test (name it here) or a written reason. `NOT_YET` is the backlog: panels that only have query-execution and column
contract coverage; the list may only shrink, and the terminal summary prints the share of panels with value assertions.
"""

# panel -> tests that assert its values
VALUE_TESTS = {
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
    'Garmin-Grafana-Dashboard.json:25': 'text panel: static content',
    'Garmin-Grafana-Dashboard.json:70': 'button: the insert is checked by test_fetch_now_button_inserts_through_its_own_datasource_outside_the_kiosk',
    'Garmin-Health-Overview.json:41': 'the version of the application: not a health value',
}

# Panels with no value assertions yet (query execution, column contract and isolation only)
NOT_YET = {
    'Garmin-Grafana-Dashboard.json:10',  # barchart: Daily Calories
    'Garmin-Grafana-Dashboard.json:11',  # timeseries: Daily avg health metrics
    'Garmin-Grafana-Dashboard.json:12',  # barchart: Sleep Score
    'Garmin-Grafana-Dashboard.json:13',  # barchart: Sleep Stages
    'Garmin-Grafana-Dashboard.json:16',  # timeseries: Steps Cumulative (24h)
    'Garmin-Grafana-Dashboard.json:17',  # barchart: Daily Activity Overview
    'Garmin-Grafana-Dashboard.json:18',  # barchart: Stress overview
    'Garmin-Grafana-Dashboard.json:19',  # barchart: Body Battery overview
    'Garmin-Grafana-Dashboard.json:20',  # marcusolsson-hourly-heatmap-panel: Hourly walk heatmap
    'Garmin-Grafana-Dashboard.json:21',  # histogram: Heart Rate Distribution
    'Garmin-Grafana-Dashboard.json:22',  # piechart: Average Sleep trends
    'Garmin-Grafana-Dashboard.json:23',  # piechart: Avg Activity over given duration
    'Garmin-Grafana-Dashboard.json:27',  # Sleep Regularity
    'Garmin-Grafana-Dashboard.json:3',  # barchart: Daily Distance
    'Garmin-Grafana-Dashboard.json:32',  # gauge: Last Sleep Hours
    'Garmin-Grafana-Dashboard.json:35',  # piechart: Today Activity
    'Garmin-Grafana-Dashboard.json:36',  # piechart: Last Sleep
    'Garmin-Grafana-Dashboard.json:37',  # barchart: Body Battery Level Change
    'Garmin-Grafana-Dashboard.json:38',  # table: Recent Activity
    'Garmin-Grafana-Dashboard.json:4',  # barchart: Daily Intensity Minutes
    'Garmin-Grafana-Dashboard.json:41',  # state-timeline: Activities
    'Garmin-Grafana-Dashboard.json:43',  # piechart: Activity HR Zones (mean)
    'Garmin-Grafana-Dashboard.json:44',  # piechart: Activity Calories (mean)
    'Garmin-Grafana-Dashboard.json:46',  # barchart: GPS Track Altitude
    'Garmin-Grafana-Dashboard.json:47',  # barchart: GPS Track pace
    'Garmin-Grafana-Dashboard.json:48',  # barchart: Activity HR difference (Max-Avg)
    'Garmin-Grafana-Dashboard.json:49',  # geomap: GPS Track by Heart Rate
    'Garmin-Grafana-Dashboard.json:50',  # geomap: GPS Track by Velocity
    'Garmin-Grafana-Dashboard.json:51',  # timeseries: Daily avg health metrics
    'Garmin-Grafana-Dashboard.json:52',  # timeseries: Sleep Movement Activity
    'Garmin-Grafana-Dashboard.json:53',  # barchart: Activity Distance
    'Garmin-Grafana-Dashboard.json:54',  # barchart: Activity Duration
    'Garmin-Grafana-Dashboard.json:55',  # xychart: Heart Rate vs Pace
    'Garmin-Grafana-Dashboard.json:57',  # trend: Distance vs HR
    'Garmin-Grafana-Dashboard.json:58',  # barchart: HR Range (Max-Min)
    'Garmin-Grafana-Dashboard.json:6',  # timeseries: Sleep Stages
    'Garmin-Grafana-Dashboard.json:61',  # barchart: GPS Track Cadence
    'Garmin-Grafana-Dashboard.json:62',  # timeseries: Breathing Rates
    'Garmin-Grafana-Dashboard.json:63',  # timeseries: Race Predictions
    'Garmin-Grafana-Dashboard.json:64',  # timeseries: VO2 Max
    'Garmin-Grafana-Dashboard.json:65',  # heatmap: HR Histogram Heatmap
    'Garmin-Grafana-Dashboard.json:66',  # state-timeline: Month at a Glance (Daily Average)
    'Garmin-Grafana-Dashboard.json:69',  # geomap: Heatmap for GPS Activity (within selected time)
    'Garmin-Grafana-Dashboard.json:7',  # timeseries: Sleep Intraday Stats
    'Garmin-Health-Overview.json:10',  # state-timeline: Sleep stages (last 24 hours)
    'Garmin-Health-Overview.json:11',  # bargauge: Last sleep by stage
    'Garmin-Health-Overview.json:15',  # timeseries: Body Battery
    'Garmin-Health-Overview.json:16',  # timeseries: Average stress
    'Garmin-Health-Overview.json:21',  # timeseries: Breathing rate during sleep
    'Garmin-Health-Overview.json:22',  # timeseries: SpO2 during sleep
    'Garmin-Health-Overview.json:23',  # timeseries: Skin temperature change
    'Garmin-Health-Overview.json:26',  # timeseries: Intensity minutes per week
    'Garmin-Health-Overview.json:30',  # timeseries: Fitness age
    'Garmin-Health-Overview.json:33',  # timeseries: Training load: acute vs chronic
    'Garmin-Health-Overview.json:34',  # timeseries: Training readiness (first reading of the day)
    'Garmin-Health-Overview.json:38',  # timeseries: Heart rate
    'Garmin-Health-Overview.json:39',  # timeseries: Stress and Body Battery
    'Garmin-Ski-Season.json:11',  # timeseries: Training time per week by discipline
    'Garmin-Ski-Season.json:13',  # timeseries: Time in heart-rate zones per week
    'Garmin-Ski-Season.json:16',  # timeseries: Training load per week
    'Garmin-Ski-Season.json:21',  # timeseries: Vertical on snow per week
    'Garmin-Ski-Season.json:23',  # timeseries: Training load: acute vs chronic
    'Garmin-Ski-Season.json:24',  # timeseries: Training readiness (first reading of the day)
    'Garmin-Ski-Season.json:3',  # stat: Vertical / week, 4 weeks
    'Garmin-Strength-Training-Dashboard.json:1009',  # barchart: Training Volume per Workout
    'Garmin-Strength-Training-Dashboard.json:1012',  # piechart: Volume by Category
    'Garmin-Strength-Training-Dashboard.json:1014',  # table: Recent Sets (Workout Detail)
    'Garmin-Strength-Training-Dashboard.json:1018',  # barchart: Time Under Tension by Exercise
    'Garmin-Strength-Training-Dashboard.json:1021',  # state-timeline: Workout Calendar
}
