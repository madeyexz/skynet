"""Curated evidence: JMIC incidents, threat levels, market rate bands, listed area, routes.

Everything here comes from a cited public source. Incident positions are geocoded by hand
from the JMIC narrative ("16 NM east of Khasab"); entries without a stated position are
placed at the transit lane and marked approx=True.
"""

JMIC = {
    109: "https://www.ukmto.org/-/media/ukmto/products/jmic_advisory_update_109_29-oct.pdf",
    110: "https://www.ukmto.org/-/media/ukmto/products/jmic_advisory_update_110_01-nov.pdf",
    111: "https://www.ukmto.org/-/media/ukmto/products/jmic_advisory_update_111_05-nov.pdf",
    112: "https://www.ukmto.org/-/media/ukmto/products/jmic_advisory_update_112_08-nov.pdf",
}

# kind weights used by the score: Attack 1.0, Enforcement 0.5, Suspicious 0.4, Advisory 0.25
INCIDENTS = [
    dict(ref="UKMTO 182-26", date="2026-10-23", vessel="GHANA PROSPERITY", vtype="Crude tanker", kind="Attack", region="Strait of Hormuz", lat=26.35, lon=56.50, approx=True, upd=109,
         text="Hit by an unknown projectile ~2 NM off Musandam while outbound; disabled and anchored. Crew safe."),
    dict(ref="UKMTO 183-26", date="2026-10-26", vessel="Name withheld", vtype="Unknown", kind="Suspicious", region="Arabian Gulf", lat=29.24, lon=49.33, approx=False, upd=109,
         text="Vessel observed on fire ~63 NM SE of Al-Faw, Iraq. Cause and crew status unknown."),
    dict(ref="UKMTO 184-26", date="2026-10-28", vessel="Multiple", vtype="Unknown", kind="Advisory", region="Gulf of Aden", lat=12.40, lon=45.80, approx=True, upd=109,
         text="Suspicious small craft near merchant ships; one carried ladders and a hook, nine on board. Suspected pirate action group."),
    dict(ref="UKMTO 185-26", date="2026-10-29", vessel="Unknown", vtype="Unknown", kind="Suspicious", region="Strait of Hormuz", lat=26.33, lon=56.25, approx=False, upd=109,
         text="Vessel observed on fire 9 NM north of Khasab, Oman."),
    dict(ref="UKMTO 186-26", date="2026-10-29", vessel="RITI", vtype="Tanker", kind="Attack", region="Strait of Hormuz", lat=26.22, lon=56.30, approx=True, upd=110,
         text="Struck by an unknown projectile outbound near Khasab; fire, lost propulsion. Crew safe."),
    dict(ref="UKMTO 187-26", date="2026-10-29", vessel="LOUISE 1", vtype="Crude tanker", kind="Attack", region="Strait of Hormuz", lat=26.47, lon=56.58, approx=False, upd=110,
         text="Small boat attached two explosive devices to the hull ~25 NM NE of Khasab; engine-room fire."),
    dict(ref="UKMTO 188-26", date="2026-10-30", vessel="AL MAYEDA", vtype="LNG carrier", kind="Attack", region="Arabian Gulf", lat=26.13, lon=53.25, approx=False, upd=110,
         text="Struck by projectiles ~40 NM south of Lavan Island, Iran; fire on board. Crew safe."),
    dict(ref="UKMTO 189-26", date="2026-10-28", vessel="ARZANAH", vtype="VLCC", kind="Attack", region="Strait of Hormuz", lat=26.52, lon=56.48, approx=True, upd=110,
         text="Struck by an unknown projectile while outbound through the strait; damaged. Crew safe."),
    dict(ref="MSCIO 160/26", date="2026-10-30", vessel="Unknown", vtype="Merchant", kind="Suspicious", region="Gulf of Aden", lat=11.96, lon=45.02, approx=False, upd=110,
         text="Armed skiff closed to ~300 m ~50 NM south of Aden; aborted when the security team showed weapons."),
    dict(ref="UKMTO 190-26", date="2026-10-29", vessel="AZINI", vtype="Fishing vessel", kind="Suspicious", region="Somali Basin", lat=12.00, lon=55.30, approx=True, upd=110,
         text="Hijacked by armed pirates ESE of Socotra; assessed as a possible mother ship. Liberated 2 Nov."),
    dict(ref="UKMTO 191-26", date="2026-11-01", vessel="SELAT STORM", vtype="Tug", kind="Attack", region="Strait of Hormuz", lat=26.37, lon=56.36, approx=False, upd=111,
         text="Struck by an unknown projectile; sank ~0.6 NM west of Jazirat Umm al Ghanam."),
    dict(ref="UKMTO 192-26", date="2026-11-01", vessel="LARGO ATOM", vtype="Tanker", kind="Attack", region="Arabian Gulf", lat=28.42, lon=49.86, approx=False, upd=111,
         text="Two projectiles hit the port side ~72 NM east of Ras Al Khafji, Saudi Arabia."),
    dict(ref="UKMTO 193-26", date="2026-11-01", vessel="Name withheld", vtype="Unknown", kind="Attack", region="Strait of Hormuz", lat=26.05, lon=56.53, approx=False, upd=111,
         text="Unknown projectile holed the starboard side ~9 NM NE of Limah, Oman."),
    dict(ref="UKMTO 194-26", date="2026-11-02", vessel="MSV AL JUSUFI", vtype="Dhow", kind="Attack", region="Strait of Hormuz", lat=26.50, lon=56.55, approx=True, upd=111,
         text="Indian-crewed dhow attacked outbound; one crew member killed, 13 rescued by the Royal Navy of Oman."),
    dict(ref="UKMTO 195-26", date="2026-11-03", vessel="Name withheld", vtype="Tanker", kind="Attack", region="Strait of Hormuz", lat=26.11, lon=56.59, approx=False, upd=111,
         text="Two unknown projectiles hit the engine room ~14 NM NE of Limah, Oman."),
    dict(ref="MSCIO 168/26", date="2026-11-03", vessel="Unknown", vtype="Unknown", kind="Suspicious", region="Bab el-Mandeb", lat=12.60, lon=43.38, approx=True, upd=111,
         text="Large floating object, possibly a boat, surrounded by smoke in the Bab el-Mandeb."),
    dict(ref="UKMTO 196-26", date="2026-11-04", vessel="Name withheld", vtype="Tanker", kind="Attack", region="Arabian Gulf", lat=26.96, lon=51.21, approx=False, upd=111,
         text="Two unidentified projectiles ~50 NM north of Madinat ash Shamal, Qatar."),
    dict(ref="UKMTO 197-26", date="2026-11-05", vessel="Name withheld", vtype="Tanker", kind="Attack", region="Strait of Hormuz", lat=26.18, lon=56.55, approx=False, upd=112,
         text="Explosion at the waterline, port side, ~16 NM east of Khasab while outbound. Fifth attack off Musandam since 1 Nov."),
    dict(ref="MSCIO 172/26", date="2026-11-06", vessel="Unidentified", vtype="Small craft", kind="Suspicious", region="Bab el-Mandeb", lat=12.773, lon=43.300, approx=False, upd=112,
         text="Unlit radar target heading to Yemen exploded at 12°46.4'N 043°18.0'E; fire on the sea surface >30 min."),
    dict(ref="UKMTO 198-26", date="2026-11-07", vessel="HUDA", vtype="Bulk carrier", kind="Enforcement", region="Gulf of Oman", lat=25.34, lon=56.68, approx=False, upd=112,
         text="Disabled by US forces ~18 NM east of Khor Fakkan for trying to run the blockade of Iranian ports."),
    dict(ref="UKMTO 199-26", date="2026-11-07", vessel="Name withheld", vtype="Tanker", kind="Attack", region="Strait of Hormuz", lat=26.27, lon=56.14, approx=False, upd=112,
         text="Tanker on fire ~8 NM NW of Khasab; crew evacuated."),
    dict(ref="UKMTO 200-26", date="2026-11-07", vessel="Name withheld", vtype="Tanker", kind="Attack", region="Strait of Hormuz", lat=26.48, lon=56.42, approx=True, upd=112,
         text="Struck by an unknown projectile while outbound through the strait. Crew safe."),
]

KIND_WEIGHT = {"Attack": 1.0, "Enforcement": 0.5, "Suspicious": 0.4, "Advisory": 0.25}

# JMIC Update 112 (8 Nov 2026), section 3, regional threat levels. Boxes are checked in order.
LEVEL_VALUE = {"LOW": 0.15, "MODERATE": 0.4, "SUBSTANTIAL": 0.7, "SEVERE": 1.0, "CRITICAL": 1.0}
ZONES = [
    ("Strait of Hormuz", "SEVERE", (25.9, 27.0, 55.8, 57.2)),
    ("Arabian Gulf", "SUBSTANTIAL", (23.5, 30.6, 47.5, 56.6)),
    ("Gulf of Oman", "MODERATE", (22.3, 25.9, 56.0, 61.8)),
    ("Bab el-Mandeb / Southern Red Sea", "SUBSTANTIAL", (11.5, 20.0, 37.5, 43.8)),
    ("Gulf of Aden", "SUBSTANTIAL", (10.5, 15.5, 43.8, 52.0)),
    ("Northern Red Sea / Suez", "MODERATE", (20.0, 31.0, 32.0, 39.0)),
    ("Arabian Sea", "MODERATE", (5.0, 25.0, 52.0, 70.0)),
    ("Somali Basin", "MODERATE", (-5.0, 10.5, 41.0, 60.0)),
]

JMIC_STATS = {
    "incidents_since_mar1": 177,
    "attacks_week_to_nov6": 11,
    "us_facilitated_per_day": 30,
    "anchored_east_of_hormuz": 208,
    "windward_jammed": "154 of 7,100 vessels transmitting in the Gulf and Gulf of Oman were jammed on 5 Nov (Windward, via JMIC 112)",
}

# Market rate bands, % of hull value per transit / 7-day period.
RATES = {
    "prewar": {"pct": 0.25, "src": "Marsh: ~0.25% before the conflict", "url": "https://www.marsh.com/sg/industries/marine/insights/strait-of-hormuz-shipping-disruption-supply-chain-resilience.html"},
    "hormuz": {"lo": 3.0, "hi": 10.0, "label": "Hormuz transit, market band",
               "src": "Marsh: rates rose back to 3%–10% of vessel value after the ceasefire broke down", "url": "https://www.marsh.com/sg/industries/marine/insights/strait-of-hormuz-shipping-disruption-supply-chain-resilience.html"},
    "hormuz_tanker": {"lo": 6.0, "hi": 10.0, "label": "Hormuz transit, tankers (Oct quotes)",
                      "src": "Brokers via Insurance Business, 14 Oct 2026: 6%–10% for tankers", "url": "https://www.alupetra.com/insights/hormuz-tanker-freight-rates-war-risk-insurance-2026.html"},
    "redsea": {"lo": 0.4, "hi": 1.0, "label": "Red Sea / Bab el-Mandeb, no exposed links",
               "src": "Marsh data: Red Sea hull war ~0.4% → ~1% in August, easing for unlinked ships", "url": "https://more.cy/red-sea-war-risks-october-2026"},
    "redsea_linked": {"lo": 1.0, "hi": 3.0, "label": "Red Sea, Saudi/US/UK/Israel-linked",
                      "src": "Reuters (late Sept): Saudi-linked tankers calling Yanbu ~3%", "url": "https://www.alupetra.com/insights/hormuz-tanker-freight-rates-war-risk-insurance-2026.html"},
}

SOURCES = {
    "lloyds_nexus": ("Lloyd's List: US, UK and Israeli ships charged ~3x more for Middle East war cover", "https://www.lloydslist.com/LL1156502/US-UK-and-Israeli-ships-charged-three-times-more-than-others-for-Middle-East-war-cover"),
    "britannia": ("Britannia P&I, War Risks Explanatory Note, 17 Mar 2026", "https://britanniapandi.com/wp-content/uploads/2026/03/War-Risks-Explanatory-Note-03-2026.pdf"),
    "jwla033": ("JWC Listed Areas JWLA-033, 3 Mar 2026", "https://lmalloyds.com/wp-content/uploads/2026/03/JWLA-033_Iran.pdf"),
    "jwc_list": ("IUA: JWC listed-area circulars (latest JWLA-035, 16 Sep 2026)", "https://staff.iua.co.uk/IUANew/UnderwritingItems/Committees/Joint_War_Committee_Risk_List.aspx"),
    "portwatch": ("IMF PortWatch daily chokepoint transits (satellite AIS)", "https://portwatch.imf.org/pages/cb5856222a5b4105adc6ee7e880a1730"),
    "gpsjam": ("gpsjam.org daily GNSS interference from aircraft ADS-B", "https://gpsjam.org/"),
    "ofac": ("OFAC Specially Designated Nationals list (sdn.csv)", "https://www.treasury.gov/ofac/downloads/sdn.csv"),
    "almonitor": ("AFP via al-Monitor, 28 Mar 2026: Ship insurers juggle war risks", "https://www.al-monitor.com/originals/2026/03/ship-insurers-juggle-war-risks-perilous-gulf-route"),
    "rfi": ("AFP via RFI, 29 Mar 2026 (same story, full text)", "https://www.rfi.fr/en/middle-east/20260329-ship-insurers-juggle-war-risks-for-perilous-gulf-route"),
    "mot": ("Singapore MOT, written reply, 4 Aug 2026", "https://www.mot.gov.sg/news-resources/newsroom/increases-in-war-risk-insurance-premiums--marine-insurance-costs-and-freight-rates-due-to-us-iran-conflict/"),
    "reuters": ("Reuters, 6 Mar 2026: Maritime insurance premiums surge", "https://www.reuters.com/world/middle-east/maritime-insurance-premiums-surge-iran-conflict-widens-2026-03-06/"),
    "howden": ("Howden Re, Strait of Hormuz report, 27 Mar 2026", "https://www.howdenre.com/sites/howdenre.howdenprod.com/files/2026-03/HowdenRe_Strait_of_Hormuz_report_March272026.pdf"),
    "jmic112": ("JMIC Advisory Update 112, 8 Nov 2026", JMIC[112]),
}

FACTS = [
    dict(claim="Annual war cover stops at JWC listed areas; entering one needs an extra premium for that trip",
         verdict="holds", note="AFP/al-Monitor quotes this almost word for word. Technically it is an additional premium (AP) under the same war policy, which underwriters may charge “as they see fit” (Lloyd's List). Howden Re: no annual Gulf war policies are being written now; all cover is voyage-by-voyage.",
         src=["almonitor", "lloyds_nexus", "howden"]),
    dict(claim="Priced as a % of the ship's value",
         verdict="holds", note="Quoted as % of hull & machinery value per transit or per 7-day period (Marsh, Singapore MOT).",
         src=["mot"]),
    dict(claim="Hormuz went from ~0.25% to 3%–10%",
         verdict="holds", note="Marsh: ~0.25% pre-war, 3%–10% after the ceasefire broke down. Singapore MOT (July): 3.5%–7.5%, from 0.10%–0.25%. Brokers in October: 6%–10% for tankers. Rates were not a straight line: peak ~2.5%–5% in March, eased after the peace deal, rose again from July.",
         src=["mot", "reuters"]),
    dict(claim="$100M tanker: $250k vs up to $10M per trip",
         verdict="holds", note="Arithmetic is right. Marsh gives US$3M–10M for a single large tanker transit.",
         src=[]),
    dict(claim="Cover runs in 7-day periods",
         verdict="holds", note="Marsh: “normally issued in seven-day blocks.” Some markets write 7–10 days.",
         src=[]),
    dict(claim="Re-reviewed every 24 hours",
         verdict="partly", note="What is 24 hours is the quote validity (Marsh), not a daily review of bound cover. AFP: owners normally get 24 h to accept a listed-area quote, cut to 12 h for Hormuz. Changes to the listed areas themselves take 7 days’ notice (72 h if a UN P5 state is involved).",
         src=["rfi", "jwla033"]),
    dict(claim="Insurers demand no US or Israel links before covering Hormuz",
         verdict="partly", note="Not a blanket market rule, and the UK is included too. Britannia's excess War P&I buy-back excludes US/UK/Israeli owned, flagged, operated or affiliated ships unless agreed, and warrants no US/Israeli owner, UBO, flag or direct financier and no Israeli port calls since 7 Oct 2023. Hull war underwriters still quote such ships, at ~3x the rate (Lloyd's List).",
         src=["britannia", "lloyds_nexus"]),
    dict(claim="Hormuz is inside the JWC listed areas",
         verdict="holds", note="JWLA-033 (3 Mar 2026) listed the Persian/Arabian Gulf, Gulf of Oman and Indian Ocean waters, and added Bahrain, Djibouti, Kuwait, Oman and Qatar. The current list is JWLA-035 (16 Sep 2026). The JWC lists areas; it does not set prices.",
         src=["jwla033", "jwc_list"]),
]

# Approximate outline of the JWC "Persian/Arabian Gulf, Gulf of Oman, Indian Ocean, Gulf of Aden
# and Southern Red Sea" listed waters (JWLA-033 boundary points; Red Sea limit per later revisions).
LISTED_AREA = [
    [25.5, 34.6], [25.5, 36.9], [24.1, 38.0], [21.5, 39.1], [16.9, 42.5], [14.8, 42.9], [13.3, 43.25],
    [12.8, 45.0], [14.5, 49.1], [15.6, 52.2], [17.0, 54.1], [19.0, 57.8], [20.5, 58.8], [22.5, 59.8],
    [23.6, 58.6], [24.4, 56.8], [25.1, 56.4], [25.6, 56.3], [26.0, 56.1], [25.8, 55.9], [25.25, 55.3],
    [24.5, 54.4], [24.1, 52.7], [25.0, 50.8], [26.4, 50.1], [27.0, 49.6], [28.4, 48.5], [29.3, 47.9],
    [30.0, 48.4], [30.3, 48.6], [28.9, 50.8], [27.8, 52.1], [26.8, 53.3], [26.55, 54.9], [27.15, 56.25],
    [26.9, 57.2], [25.65, 57.8], [25.3, 60.6], [25.1, 62.3], [25.32, 65.0], [10.8, 65.0], [10.8, 60.25],
    [-6.75, 48.75], [-1.67, 41.57], [2.04, 45.34], [5.35, 48.5], [8.0, 49.85], [10.45, 51.4], [11.8, 51.25],
    [11.3, 49.2], [10.45, 45.0], [11.35, 43.47], [11.6, 43.15], [13.0, 42.75], [15.6, 39.45], [18.0, 38.6],
    [19.6, 37.2], [22.0, 36.9], [23.9, 35.5],
]

_ASIA_TAIL = [[25.8, 57.0], [24.8, 58.2], [22.6, 60.3], [17.0, 64.0], [10.0, 72.0], [6.2, 79.5], [5.9, 81.5],
              [5.8, 95.0], [3.5, 99.5], [1.25, 103.8], [2.0, 105.0], [10.0, 110.5], [18.0, 116.0], [22.6, 120.28]]

ROUTES = {
    "rastanura-kaohsiung": dict(label="Ras Tanura → Kaohsiung", via="Strait of Hormuz",
                                pts=[[26.64, 50.20], [26.75, 51.5], [26.55, 53.8], [26.35, 55.4], [26.58, 56.25], [26.45, 56.6]] + _ASIA_TAIL),
    "jebelali-kaohsiung": dict(label="Jebel Ali → Kaohsiung", via="Strait of Hormuz",
                               pts=[[25.02, 55.05], [25.6, 55.4], [26.25, 56.0], [26.58, 56.3], [26.45, 56.6]] + _ASIA_TAIL),
    "suez-singapore": dict(label="Suez → Singapore", via="Bab el-Mandeb",
                           pts=[[29.93, 32.56], [27.8, 33.7], [24.5, 35.8], [20.0, 38.4], [16.0, 41.0], [13.5, 42.6], [12.6, 43.35],
                                [12.2, 44.5], [12.4, 47.0], [12.8, 50.0], [12.5, 53.5], [10.0, 60.0], [7.5, 70.0], [6.0, 79.0],
                                [5.9, 81.5], [5.8, 95.0], [3.5, 99.5], [1.25, 103.8]]),
}

# Illustrative ships. Names are invented except the OFAC example, which is a real SDN entry.
PRESETS = [
    dict(id="vlcc", label="VLCC · Hormuz", route="rastanura-kaohsiung", speed_kn=13, port_days=2,
         ship=dict(name="FORMOSA DAWN (illustrative)", imo="", type="tanker", hull_usd_m=100, flag="Panama", owner_country="Taiwan",
                   nexus_us_uk_il=False, israel_call=False, saudi_link=False, ais_on=True)),
    dict(id="vlcc-us", label="Same VLCC, US financier", route="rastanura-kaohsiung", speed_kn=13, port_days=2,
         ship=dict(name="FORMOSA DAWN (illustrative)", imo="", type="tanker", hull_usd_m=100, flag="Panama", owner_country="Taiwan",
                   nexus_us_uk_il=True, israel_call=False, saudi_link=False, ais_on=True)),
    dict(id="box-gulf", label="Box ship · Jebel Ali", route="jebelali-kaohsiung", speed_kn=16, port_days=1,
         ship=dict(name="EVER-class 14k TEU (illustrative)", imo="", type="container", hull_usd_m=150, flag="Singapore", owner_country="Taiwan",
                   nexus_us_uk_il=False, israel_call=False, saudi_link=False, ais_on=True)),
    dict(id="box-redsea", label="Box ship · Red Sea", route="suez-singapore", speed_kn=16, port_days=0,
         ship=dict(name="EVER-class 14k TEU (illustrative)", imo="", type="container", hull_usd_m=150, flag="Singapore", owner_country="Taiwan",
                   nexus_us_uk_il=False, israel_call=False, saudi_link=False, ais_on=True)),
    dict(id="sdn", label="Sanctioned tanker", route="rastanura-kaohsiung", speed_kn=12, port_days=2,
         ship=dict(name="DREAM II", imo="9356593", type="tanker", hull_usd_m=40, flag="Iran", owner_country="Iran",
                   nexus_us_uk_il=False, israel_call=False, saudi_link=False, ais_on=False)),
]
