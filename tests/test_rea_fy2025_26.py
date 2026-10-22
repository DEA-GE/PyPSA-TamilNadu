from scripts.rea_fy2025_26.archive import is_provisional_zip
from scripts.rea_fy2025_26.mappings import STATIONS, ba_for_district
from scripts.rea_fy2025_26.pipeline import QUARTER_HOUR_GWH, channel_from_transaction, station_info, time_block


def test_quarter_hour_mw_to_gwh():
    assert 1000 * QUARTER_HOUR_GWH == 0.25


def test_time_conventions_are_normalized():
    assert time_block(1) == 1
    assert time_block("96") == 96
    assert time_block("00:00:00") == 1
    assert time_block("23:45:00") == 96


def test_tstpp_is_not_in_tamil_nadu():
    assert STATIONS["tstpp"][1] == "Telangana"
    assert station_info("tstpp")[2] == "Telangana"


def test_tamil_nadu_generating_stations_have_supported_locations():
    for code in ("nlc21", "nlc22", "nlcexp", "nlc2exp", "maps", "vallur", "kknpp", "ntpl", "nntpp"):
        assert STATIONS[code][1] == "Tamil Nadu"
        assert STATIONS[code][3] is not None
        assert STATIONS[code][4] is not None


def test_project_districts_map_to_nine_balancing_areas():
    assert ba_for_district("Tirunelveli") == "Tirunelveli"
    assert ba_for_district("Thoothukudi") == "Tirunelveli"
    assert ba_for_district("Chengalpattu") == "Chennai South"
    assert ba_for_district("Thiruvallur") == "Chennai North"
    assert ba_for_district("Cuddalore") == "Villupuram"
    assert ba_for_district("Karur") == "Trichy"


def test_supported_bilateral_seller_codes_have_districts():
    assert STATIONS["opgpgplu4tn"][2] == "Thiruvallur"
    assert STATIONS["tulsyannecltd"][2] == "Thiruvallur"
    assert STATIONS["eidparrypugalur"][2] == "Karur"


def test_high_energy_import_sellers_have_verified_physical_states():
    assert STATIONS["kskmahanadi"][1] == "Chhattisgarh"
    assert STATIONS["jpl2"][1] == "Chhattisgarh"
    assert STATIONS["dbpl"][1] == "Chhattisgarh"
    assert STATIONS["balco"][1] == "Chhattisgarh"
    assert STATIONS["gmrwarora"][1] == "Maharashtra"
    assert STATIONS["coastgen"][1] == "Tamil Nadu"
    assert STATIONS["ilfs"][1] == "Tamil Nadu"


def test_transaction_channel_normalization():
    assert channel_from_transaction("GNA") == "GNA"
    assert channel_from_transaction("TGNA") == "TGNA"
    assert channel_from_transaction("RNW") == "REMC/RNW"


def test_provisional_archive_detection():
    assert is_provisional_zip("reapmar26.zip")
    assert is_provisional_zip("may25p (1).zip")
    assert not is_provisional_zip("reamar26f.zip")
    assert not is_provisional_zip("IPP PPA Wise details.zip")


def test_march_composite_identity_example():
    lta = 5017.49
    gna = 2690.47
    tgna = 2596.87
    market = (117.09 - 65.60) + (33.16 - 16.70) + 66.03
    loss = -170.38
    assert abs((lta + gna + tgna + market + loss) - 10268.43) < 1e-9

