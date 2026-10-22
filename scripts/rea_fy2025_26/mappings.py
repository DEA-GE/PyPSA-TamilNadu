"""Audited entity and station mappings used by the REA pipeline.

Only mappings supported by the source names or the repository's plant registry are
included. Unknown entities deliberately remain unknown.
"""

from __future__ import annotations

MONTH_ARCHIVES = [
    ("2025-04", "reaapr25f.zip"),
    ("2025-05", "may25f.zip"),
    ("2025-06", "reajune25f.zip"),
    ("2025-07", "july25f.zip"),
    ("2025-08", "reaaug25f.zip"),
    ("2025-09", "reasep25f.zip"),
    ("2025-10", "reaoct25f.zip"),
    ("2025-11", "reanov25f.zip"),
    ("2025-12", "reafdec25.zip"),
    ("2026-01", "reafjan26.zip"),
    ("2026-02", "reafeb26f.zip"),
    ("2026-03", "reamar26f.zip"),
]

BENEFICIARY_FILES = {
    "andhrapradesh": "Andhra Pradesh",
    "bihar": "Bihar",
    "goa": "Goa",
    "jk": "Jammu and Kashmir",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "ndmc": "Delhi",
    "orrissa": "Odisha",
    "odisha": "Odisha",
    "pondichery": "Puducherry",
    "puducherry": "Puducherry",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "tamilnadu": "Tamil Nadu",
    "telangana": "Telangana",
    "uttarakhand": "Uttarakhand",
    "uttarpradesh": "Uttar Pradesh",
}

# Code -> (display name, physical state, district, longitude, latitude).
# Coordinates are used only to intersect the supplied nine-BA geopackage.
STATIONS = {
    "rstps": ("Ramagundam STPS I-II", "Telangana", "Peddapalli", None, None),
    "rstps3": ("Ramagundam STPS III", "Telangana", "Peddapalli", None, None),
    "nlc21": ("Neyveli TPS II Stage I", "Tamil Nadu", "Cuddalore", 79.4757, 11.5927),
    "nlc22": ("Neyveli TPS II Stage II", "Tamil Nadu", "Cuddalore", 79.4757, 11.5927),
    "nlcexp": ("Neyveli TPS I Expansion", "Tamil Nadu", "Cuddalore", 79.4476, 11.5927),
    "kaps": ("Kakrapar Atomic Power Station I-II", "Gujarat", "Tapi", None, None),
    "kaps12": ("Kakrapar Atomic Power Station I-II", "Gujarat", "Tapi", None, None),
    "kaps34": ("Kakrapar Atomic Power Station III-IV", "Gujarat", "Tapi", None, None),
    "maps": ("Madras Atomic Power Station", "Tamil Nadu", "Chengalpattu", 80.1750, 12.5570),
    "talcher2": ("Talcher STPS II", "Odisha", "Angul", None, None),
    "talcher": ("Talcher STPS", "Odisha", "Angul", None, None),
    "kalalgaon": ("Kahalgaon STPS", "Bihar", "Bhagalpur", None, None),
    "kahalgaon": ("Kahalgaon STPS", "Bihar", "Bhagalpur", None, None),
    "faraka": ("Farakka STPS", "West Bengal", "Murshidabad", None, None),
    "fstpp3": ("Farakka STPS III", "West Bengal", "Murshidabad", None, None),
    "simhadri1": ("Simhadri STPS I", "Andhra Pradesh", "Visakhapatnam", None, None),
    "simhadri2": ("Simhadri STPS II", "Andhra Pradesh", "Visakhapatnam", None, None),
    "igstps": ("Indira Gandhi STPS", "Haryana", "Jhajjar", None, None),
    "vallur": ("NTECL Vallur STPS", "Tamil Nadu", "Thiruvallur", 80.2780, 13.2570),
    "kknpp": ("Kudankulam Nuclear Power Project I", "Tamil Nadu", "Tirunelveli", 77.7120, 8.1690),
    "kknppu2": ("Kudankulam Nuclear Power Project II", "Tamil Nadu", "Tirunelveli", 77.7120, 8.1690),
    "nlc2exp": ("Neyveli TPS II Expansion", "Tamil Nadu", "Cuddalore", 79.4416, 11.5576),
    "ntpl": ("NLC Tamil Nadu Power Ltd Tuticorin", "Tamil Nadu", "Thoothukudi", 78.1711, 8.7604),
    "kudgi1": ("Kudgi STPS I", "Karnataka", "Vijayapura", None, None),
    "kstps": ("Korba STPS", "Chhattisgarh", "Korba", None, None),
    "kstps7": ("Korba STPS VII", "Chhattisgarh", "Korba", None, None),
    "vstps1": ("Vindhyachal STPS I", "Madhya Pradesh", "Singrauli", None, None),
    "vstps2": ("Vindhyachal STPS II", "Madhya Pradesh", "Singrauli", None, None),
    "vstps3": ("Vindhyachal STPS III", "Madhya Pradesh", "Singrauli", None, None),
    "vstps4": ("Vindhyachal STPS IV", "Madhya Pradesh", "Singrauli", None, None),
    "vstps5": ("Vindhyachal STPS V", "Madhya Pradesh", "Singrauli", None, None),
    "sipat1": ("Sipat STPS I", "Chhattisgarh", "Bilaspur", None, None),
    "sipat2": ("Sipat STPS II", "Chhattisgarh", "Bilaspur", None, None),
    "mstps1": ("Mouda STPS I", "Maharashtra", "Nagpur", None, None),
    "mstps2": ("Mouda STPS II", "Maharashtra", "Nagpur", None, None),
    "sstpp": ("Solapur STPP", "Maharashtra", "Solapur", None, None),
    "rgppl": ("Ratnagiri Gas and Power", "Maharashtra", "Ratnagiri", None, None),
    "khstpp2": ("Kahalgaon STPS II", "Bihar", "Bhagalpur", None, None),
    "barh": ("Barh STPP", "Bihar", "Patna", None, None),
    "barh1": ("Barh STPP I", "Bihar", "Patna", None, None),
    "gadarwara1": ("Gadarwara STPS I", "Madhya Pradesh", "Narsinghpur", None, None),
    "dadrt2": ("Dadri TPP II", "Uttar Pradesh", "Gautam Buddha Nagar", None, None),
    "lara1": ("Lara STPS I", "Chhattisgarh", "Raigarh", None, None),
    "nntpp": ("Neyveli New Thermal Power Project", "Tamil Nadu", "Cuddalore", 79.5014, 11.5486),
    "khargone1": ("Khargone STPP I", "Madhya Pradesh", "Khargone", None, None),
    "brbcl": ("BRBCL Nabinagar", "Bihar", "Aurangabad", None, None),
    "bgtpp": ("Bongaigaon TPP", "Assam", "Kokrajhar", None, None),
    "unchahar": ("Feroze Gandhi Unchahar TPS", "Uttar Pradesh", "Raebareli", None, None),
    "npgc": ("NPGC Nabinagar", "Bihar", "Aurangabad", None, None),
    "agbpp": ("Agartala Gas Based Power Plant", "Tripura", "West Tripura", None, None),
    "agtccpp": ("Agartala Gas Turbine CCPP", "Tripura", "West Tripura", None, None),
    "tstpp": ("Telangana STPP", "Telangana", "Peddapalli", None, None),
    # Bilateral seller codes matched to the project's existing-plant registry.
    "opgpgplu4tn": ("OPG Power Generation Private Ltd Unit 4", "Tamil Nadu", "Thiruvallur", None, None),
    "opgpgunit1tn": ("OPG Power Generation Private Ltd Unit 1", "Tamil Nadu", "Thiruvallur", None, None),
    "opgpgplu2tn": ("OPG Power Generation Private Ltd Unit 2", "Tamil Nadu", "Thiruvallur", None, None),
    "tulsyannecltd": ("Tulsyan NEC Limited", "Tamil Nadu", "Thiruvallur", None, None),
    "eidparrypugalur": ("EID Parry India Ltd Pugalur", "Tamil Nadu", "Karur", None, None),
    "kamachiltdtn": ("Kamachi Steels Ltd", "Tamil Nadu", "Tirunelveli", None, None),
    "arkayenergytn": ("ARKAY Energy Ltd", "Tamil Nadu", "Theni", None, None),
    # High-energy TNEB importer-application sellers verified against plant-owner
    # or government sources.  TN-local sellers are intentionally identified so
    # their schedules are excluded as TN-to-TN rather than treated as imports.
    "kskmahanadi": ("KSK Mahanadi Power Plant", "Chhattisgarh", "Janjgir-Champa", None, None),
    "jpl2": ("Jindal Power Ltd Tamnar", "Chhattisgarh", "Raigarh", None, None),
    "coastgen": ("Coastal Energen Pvt Ltd", "Tamil Nadu", "Thoothukudi", None, None),
    "ilfs": ("IL&FS Tamil Nadu Power Company Ltd", "Tamil Nadu", "Cuddalore", None, None),
    "dbpl": ("DB Power Ltd", "Chhattisgarh", "Janjgir-Champa", None, None),
    "balco": ("Bharat Aluminium Company Ltd", "Chhattisgarh", "Korba", None, None),
    "gmrwarora": ("GMR Warora Energy Ltd", "Maharashtra", "Chandrapur", None, None),
    "dhariwalists": ("Dhariwal Infrastructure Ltd", "Maharashtra", "Chandrapur", None, None),
    "jpnigriejnstpp": ("Jaypee Nigrie STPP", "Madhya Pradesh", "Singrauli", None, None),
    "sksraigarh": ("SKS Power Generation Ltd", "Chhattisgarh", "Raigarh", None, None),
}

# Workbook-supported seller-code links. Row numbers are pandas zero-based indices
# from the named sheet; the source-row value is retained where the sheet provides it.
STATION_MAPPING_EVIDENCE = {
    "opgpgplu4tn": "PlantInfo row 45: OPG Power Generation Private Ltd.; Thiruvallur",
    "opgpgunit1tn": "PlantInfo row 45: OPG Power Generation Private Ltd.; Thiruvallur",
    "opgpgplu2tn": "PlantInfo row 45: OPG Power Generation Private Ltd.; Thiruvallur",
    "tulsyannecltd": "PlantInfo row 50: Tulsyan NEC Limited; Thiruvallur",
    "eidparrypugalur": "PlantInfo row 232: EID Parry India Ltd. Tamil Nadu Karur; Karur",
    "kamachiltdtn": "Prayas TN Records row 2298 / source CSV row 8928: KAMACHI STEELS LTD.; Tirunelveli",
    "arkayenergytn": "Prayas TN Records row 7003 / source CSV row 13633: M/s. ARKAY Energy Ltd; Theni",
    "kskmahanadi": "KSK Mahanadi Power / JSW Energy: Nariyara, Janjgir-Champa, Chhattisgarh",
    "jpl2": "Jindal Power: Tamnar, Raigarh, Chhattisgarh",
    "coastgen": "Coastal Energen: Melamaruthur, Thoothukudi, Tamil Nadu",
    "ilfs": "IL&FS Tamil Nadu Power Company: Cuddalore, Tamil Nadu",
    "dbpl": "DB Power: Baradarha, Janjgir-Champa, Chhattisgarh",
    "balco": "BALCO: Korba, Chhattisgarh",
    "gmrwarora": "GMR Warora Energy: Chandrapur, Maharashtra",
    "dhariwalists": "WRPC distribution list: Dhariwal Infrastructure, Chandrapur, Maharashtra",
    "jpnigriejnstpp": "WRPC distribution list: Jaypee Nigrie STPP, Singrauli, Madhya Pradesh",
    "sksraigarh": "SKS Power Generation: Raigarh, Chhattisgarh",
}

# Project-authoritative district aggregation from data/model_inputs/Balancing_areas.txt.
# Alternate spellings are included only where they occur in the project plant registry.
DISTRICT_TO_BA = {
    "chennai": "Chennai North", "tiruvallur": "Chennai North", "thiruvallur": "Chennai North",
    "chengalpattu": "Chennai South", "kancheepuram": "Chennai South", "kanchipuram": "Chennai South",
    "ranipet": "Vellore", "vellore": "Vellore", "tirupathur": "Vellore",
    "krishnagiri": "Vellore", "dharmapuri": "Vellore",
    "tiruvannamalai": "Villupuram", "viluppuram": "Villupuram", "villupuram": "Villupuram",
    "kallakurichi": "Villupuram", "cuddalore": "Villupuram",
    "ariyalur": "Trichy", "perambalur": "Trichy", "tiruchirappalli": "Trichy",
    "thanjavur": "Trichy", "thiruvarur": "Trichy", "mayiladuthurai": "Trichy",
    "nagapattinam": "Trichy", "pudukkottai": "Trichy", "karur": "Trichy",
    "salem": "Erode", "namakkal": "Erode", "erode": "Erode",
    "thenilgiris": "Coimbatore", "nilgiris": "Coimbatore", "coimbatore": "Coimbatore", "tiruppur": "Coimbatore",
    "dindigul": "Madurai", "theni": "Madurai", "madurai": "Madurai",
    "sivagangai": "Madurai", "ramanathapuram": "Madurai",
    "virudhunagar": "Tirunelveli", "tenkasi": "Tirunelveli", "tirunelveli": "Tirunelveli",
    "thoothukudi": "Tirunelveli", "thoothukkudi": "Tirunelveli", "tuticorin": "Tirunelveli",
    "kanniyakumari": "Tirunelveli", "kanyakumari": "Tirunelveli",
}


def ba_for_district(district: object) -> str:
    """Return the documented nine-BA aggregation for a Tamil Nadu district."""
    token = "".join(character for character in str(district).lower() if character.isalnum())
    return DISTRICT_TO_BA.get(token, "")

STATE_ALIASES = {
    "andhrapradesh": "Andhra Pradesh",
    "ap": "Andhra Pradesh",
    "arunachalpradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh",
    "delhi": "Delhi",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachalpradesh": "Himachal Pradesh",
    "jammukashmir": "Jammu and Kashmir",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "madhyapradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "tamilnadu": "Tamil Nadu",
    "tangedco": "Tamil Nadu",
    "tneb": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttarakhand": "Uttarakhand",
    "uttarpradesh": "Uttar Pradesh",
    "westbengal": "West Bengal",
}

TN_TOKEN_TO_STATION = {
    "kknpp": "kknpp",
    "kudankulam": "kknpp",
    "vallur": "vallur",
    "ntecl": "vallur",
    "nntpp": "nntpp",
    "neyvelinew": "nntpp",
    "ntpl": "ntpl",
    "tuticorinjv": "ntpl",
    "maps": "maps",
    "kalpakkam": "maps",
    "nlc2exp": "nlc2exp",
    "nlcexp": "nlcexp",
    "nlc21": "nlc21",
    "nlc22": "nlc22",
}

BA_NAMES = [
    "Chennai North",
    "Chennai South",
    "Coimbatore",
    "Erode",
    "Madurai",
    "Tirunelveli",
    "Trichy",
    "Vellore",
    "Villupuram",
]

# Columns in state composite schedules that are accounting components rather
# than station-to-beneficiary allocations.
COMPOSITE_COMPONENTS = {
    "record_id", "date", "time", "sch_total", "loss", "lta", "lta_imp", "lta_exp",
    "mtoa_imp", "mtoa_exp", "stoa_imp", "stoa_exp", "px_imp", "px_exp", "rtm_imp",
    "rtm_exp", "gdam_imp", "gdam_exp", "hpdam_imp", "hpdam_exp", "remc_imp",
    "remc_exp", "solar_net", "wind_net", "other_net", "shared", "shared_loss",
    "gna_net", "tgna_imp", "tgna_exp", "tgna_net", "unnamed: 82", "unnamed: 81",
}

