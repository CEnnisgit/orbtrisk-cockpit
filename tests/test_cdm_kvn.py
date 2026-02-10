from datetime import datetime

from app.services.cdm_kvn import parse_cdm_kvn


def test_parse_cdm_kvn_minimal():
    tca = datetime.utcnow().isoformat()
    kvn = "\n".join(
        [
            "CCSDS_CDM_VERS = 1.0",
            f"CREATION_DATE = {datetime.utcnow().isoformat()}",
            "ORIGINATOR = TEST",
            f"TCA = {tca}",
            "REF_FRAME = GCRS",
            "MISS_DISTANCE = 20.0 [m]",
            "RELATIVE_SPEED = 10.0 [m/s]",
            "OBJECT = OBJECT1",
            "NORAD_CAT_ID = 10000",
            "OBJECT_NAME = ALPHA",
            "X = 7000.0 [km]",
            "Y = 0.0 [km]",
            "Z = 0.0 [km]",
            "X_DOT = 0.0 [km/s]",
            "Y_DOT = 7.5 [km/s]",
            "Z_DOT = 0.0 [km/s]",
            "OBJECT = OBJECT2",
            "NORAD_CAT_ID = 12345",
            "OBJECT_NAME = CATALOG-DELTA",
            "X = 7000.02 [km]",
            "Y = 0.0 [km]",
            "Z = 0.0 [km]",
            "X_DOT = 0.0 [km/s]",
            "Y_DOT = 7.51 [km/s]",
            "Z_DOT = 0.0 [km/s]",
            "CR_R = 100.0 [m^2]",
            "CT_R = 0.0 [m^2]",
            "CT_T = 100.0 [m^2]",
            "CN_R = 0.0 [m^2]",
            "CN_T = 0.0 [m^2]",
            "CN_N = 100.0 [m^2]",
            "",
        ]
    )

    parsed = parse_cdm_kvn(kvn)
    assert parsed.version == "1.0"
    assert parsed.ref_frame == "GCRS"
    assert parsed.miss_distance_km == 0.02
    assert parsed.relative_speed_km_s == 0.01
    assert parsed.object1.norad_cat_id == 10000
    assert parsed.object2.norad_cat_id == 12345
    assert parsed.covariance_rtn_km2 is not None
    assert abs(parsed.covariance_rtn_km2[0][0] - 0.0001) < 1e-9

