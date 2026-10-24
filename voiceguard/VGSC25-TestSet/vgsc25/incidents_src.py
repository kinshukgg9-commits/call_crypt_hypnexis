"""Source of incidents.jsonl and sources.csv: documented voice scam calls of 2025.

Edit here, then run `python benchmark/vgsc25/incidents_src.py`. Every fact in a record must be
supported by at least one of its sources; `supports` says which fields each source backs.
Private victims are never named. Public officials whose voice or office was impersonated are.
"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCESSED = "2026-10-24"
# Approximate 2025 average rates, for scale only.
USD = {"INR": 1 / 86.0, "EUR": 1.13, "SGD": 0.76, "USD": 1.0}

# source_type: official (government, police, court, regulator), official_media (state broadcaster
# reporting police action), news, database (AI Incident Database), wire (agency copy).
# fetched: the page was read for this release; False means it was listed by another source only.
def S(url, publisher, source_type, published, supports, fetched=True, note=""):
    return dict(url=url, publisher=publisher, source_type=source_type, published=published,
                accessed=ACCESSED if fetched else None, fetched=fetched, supports=supports, note=note)


INCIDENTS = [
    # ------------------------------------------------------------------ India: "digital arrest"
    dict(id="IN-2025-001", record_type="incident", date="2025-03-27", date_precision="day",
         country="IN", region="Belagavi, Karnataka", scam_type="digital_arrest",
         claimed_identity=["law enforcement (unspecified)"], channel=["phone"],
         ai_voice="not_reported", victim_profile="elderly couple",
         loss=5_000_000, currency="INR", recovered=None, outcome="victims died by suicide; police tracing transfers",
         tactics=["authority", "threat_of_arrest", "isolation", "money_transfer"],
         summary="An elderly couple placed under 'digital arrest' by phone callers transferred about Rs 50 lakh to several accounts, then died by suicide. Police said it was the first reported such death from fear of digital arrest.",
         sources=[S("https://www.deccanherald.com/india/karnataka/digital-arrest-claims-elderly-couple-s-lives-3468251", "Deccan Herald", "news", "2025-03-28", ["date", "region", "channel", "loss", "outcome"], note="quotes Belagavi SP"),
                  S("https://www.ndtv.com/india-news/elderly-couple-loses-rs-50-lakh-to-cyber-fraud-in-karnataka-dies-by-suicide-8035112", "NDTV", "news", "2025-03", ["loss", "outcome"], fetched=False, note="cited by Wikipedia; site blocked our reader")]),
    dict(id="IN-2025-002", record_type="incident", date="2025-02-01", date_precision="day",
         country="IN", region="Noida, Uttar Pradesh", scam_type="digital_arrest",
         claimed_identity=["telecom regulator (TRAI) referral", "Mumbai cyber crime branch", "IPS officer"],
         channel=["phone", "video_call"], ai_voice="not_reported", victim_profile="family of three (parents and daughter)",
         loss=11_000_000, currency="INR", recovered=None, outcome="case registered",
         tactics=["sim_block_threat", "authority", "money_laundering_accusation", "threat_of_arrest", "isolation", "money_transfer"],
         summary="A caller warned the family's SIM would be blocked and referred them to TRAI; a fake IPS officer then said 24 cases were registered against the father for money laundering. The wife and daughter were also told they were under digital arrest and could not leave the house. About Rs 1.1 crore was transferred over five days.",
         sources=[S("https://www.indiatvnews.com/technology/news/noida-family-fooled-in-digital-arrest-scam-lost-rs-1-crore-fraudsters-posed-as-cops-2025-02-11-975720", "India TV", "news", "2025-02-11", ["date", "region", "claimed_identity", "channel", "loss", "tactics"]),
                  S("https://www.digit.in/news/general/lost-over-rs-1-cr-noida-family-becomes-latest-victim-of-digital-scam-heres-what-happened.html", "Digit", "news", "2025-02", ["date", "region", "claimed_identity", "loss", "tactics"])]),
    dict(id="IN-2025-003", record_type="incident", date="2025-03-02", date_precision="day",
         country="IN", region="Delhi", scam_type="digital_arrest",
         claimed_identity=["TRAI official", "Mumbai Police sub-inspector", "CBI chief", "Supreme Court judge"],
         channel=["phone", "whatsapp_video"], ai_voice="not_reported", victim_profile="retired government officer",
         loss=4_850_000, currency="INR", recovered=None, outcome="3 arrested by South West Delhi cyber cell; network linked to 28 complaints",
         tactics=["authority", "aadhaar_misuse", "money_laundering_accusation", "fake_court", "isolation", "liquidate_savings", "money_transfer"],
         summary="A caller claiming to be from TRAI said a money-laundering case was registered in Mumbai under the victim's Aadhaar. Fake police, CBI and a 'Supreme Court judge' held him in 'virtual custody' over WhatsApp video for 72 hours and made him break fixed deposits and PPF into accounts said to be under court supervision.",
         sources=[S("https://www.tribuneindia.com/news/delhi/digital-arrest-scam-delhi-cyber-police-arrest-3-after-retired-officer-cheated-of-rs-48-5-lakh/amp", "The Tribune (ANI)", "wire", "2025-04-19", ["date", "region", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="IN-2025-004", record_type="incident", date="2025-05-15", date_precision="report",
         country="IN", region="Delhi", scam_type="digital_arrest",
         claimed_identity=["TRAI", "Maharashtra Police"], channel=["phone"], ai_voice="not_reported",
         victim_profile="92-year-old", loss=20_000_000, currency="INR", recovered=20_000_000,
         outcome="Delhi Police IFSO recovered over Rs 2 crore; phones and SIMs seized",
         tactics=["authority", "threat_of_arrest", "money_transfer"],
         summary="A 92-year-old was duped of over Rs 2 crore by callers posing as TRAI and Maharashtra Police. Delhi Police's IFSO unit traced and restored the money.",
         sources=[S("https://www.newsonair.gov.in/delhi-police-busts-digital-arrest-scam-recovers-₹2-crore-duped-from-92-year-old", "All India Radio News", "official_media", "2025-05-15", ["date", "claimed_identity", "loss", "recovered", "outcome"])]),
    dict(id="IN-2025-005", record_type="incident", date="2025-08", date_precision="month",
         country="IN", region="Mumbai, Maharashtra", scam_type="digital_arrest",
         claimed_identity=["TRAI", "Mumbai crime branch", "police officers", "judges (mock hearing)"],
         channel=["phone", "video_call"], ai_voice="not_reported", victim_profile="72-year-old company vice-president",
         loss=581_300_000, currency="INR", recovered=None,
         outcome="7 arrested by Oct 2025, 32 by Jan 2026; 2,500-page chargesheet; 41 wanted",
         tactics=["authority", "money_laundering_accusation", "fake_court", "isolation", "liquidate_savings", "money_transfer"],
         summary="Over about 40 days from August 2025, callers posing as TRAI, Mumbai crime branch officers and judges in staged video hearings made a 72-year-old sell shares and transfer Rs 58.13 crore through thousands of mule accounts.",
         sources=[S("https://www.newsonair.gov.in/digital-arrest-scam", "All India Radio News", "official_media", "2025-10-17", ["loss", "channel", "victim_profile", "outcome"]),
                  S("https://moneylife.in/article/rs58-crore-digital-arrest-scam-maharashtra-cyber-files-2500page-chargesheet-41-accused-still-on-the-run/79349.html", "Moneylife", "news", "2026-01-09", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="IN-2025-006", record_type="incident", date="2025-11-14", date_precision="complaint",
         country="IN", region="Bengaluru, Karnataka", scam_type="digital_arrest",
         claimed_identity=["courier company (DHL, Andheri)", "CBI officers", "RBI Financial Intelligence Unit (invoked)"],
         channel=["phone", "video_call (Skype)"], ai_voice="not_reported", victim_profile="57-year-old IT professional",
         loss=318_300_000, currency="INR", recovered=None, outcome="FIR at East Division CEN police station",
         tactics=["parcel_contraband", "identity_misuse", "authority", "threat_of_arrest", "do_not_tell_police", "surveillance", "asset_verification", "fake_clearance_letter", "money_transfer"],
         summary="A 'courier' said a parcel in her name held passports, credit cards and MDMA, then handed over to fake CBI officers who kept her under Skype surveillance and made 187 transfers 'to verify her assets', promising a refund. Calls began in September 2024; she complained in November 2025.",
         note="Fraud began 2024-09-15; included because the complaint and investigation are from 2025. Sources differ on her job (IT executive vs software engineer).",
         sources=[S("https://www.etvbharat.com/en/state/bengaluru-techie-loses-over-rs-31-crore-in-digital-arrest-scam-enn25111703151", "ETV Bharat", "news", "2025-11-17", ["date", "region", "claimed_identity", "channel", "loss", "outcome", "tactics"]),
                  S("https://www.thehitavada.com/Encyc/2025/11/18/bluru-woman-loses-rs-3183-cr-in-months-long-digital-arrest-scam.html", "The Hitavada", "news", "2025-11-18", ["claimed_identity", "channel", "loss", "tactics"])]),
    dict(id="IN-2025-007", record_type="incident", date="2025-11-07", date_precision="day",
         country="IN", region="Hyderabad, Telangana", scam_type="digital_arrest",
         claimed_identity=["CBI officials", "Delhi Crime Branch (fake FIR)"], channel=["phone", "video_call"],
         ai_voice="not_reported", victim_profile="71-year-old man", loss=19_252_070, currency="INR", recovered=None,
         outcome="3 mule-account holders arrested; prime accused absconding",
         tactics=["aadhaar_misuse", "bank_account_misuse", "authority", "fake_documents", "money_transfer"],
         summary="Callers posing as CBI officers said his Aadhaar was used to open a Canara Bank account in Mumbai for crime, showed a fake Delhi Crime Branch FIR on video, and made him deposit Rs 1.92 crore between 7 and 14 November 2025 'to clear his name'.",
         sources=[S("https://www.siasat.com/three-arrested-for-cheating-elderly-man-in-digital-arrest-case-in-hyderabad-3306641/", "Siasat", "news", "2025-11-30", ["date", "region", "claimed_identity", "loss", "outcome", "tactics"]),
                  S("https://thelogicalindian.com/71-year-old-hyderabad-man-loses-%E2%82%B91-92-crore-in-digital-arrest-scam-three-held-main-accused-absconding/", "The Logical Indian", "news", "2025-12-01", ["date", "claimed_identity", "channel", "loss", "outcome"])]),
    dict(id="IN-2025-008", record_type="policy", date="2025-12-01", date_precision="day",
         country="IN", region="national", scam_type="digital_arrest",
         claimed_identity=[], channel=[], ai_voice="not_applicable", victim_profile=None,
         loss=None, currency=None, recovered=None, outcome="Supreme Court directs a pan-India CBI investigation of digital arrest scams",
         tactics=[], summary="The Supreme Court ordered the CBI to investigate digital arrest scams across India.",
         sources=[S("https://www.moneylife.in/article/supreme-court-orders-cbi-probe-into-digital-arrest-scams/78995.html", "Moneylife", "news", "2025-12-01", ["outcome"], note="says 'on Monday'; 1 Dec 2025 was a Monday"),
                  S("https://www.medianama.com/2025/12/223-supreme-court-cbi-probe-digital-arrest-scams/", "MediaNama", "news", "2025-12", ["date", "outcome"], fetched=False),
                  S("https://www.onmanorama.com/news/india/2025/12/01/digital-arrest-scams-cbi-investigation-sc-order.amp.html", "Onmanorama", "news", "2025-12-01", ["date", "outcome"], fetched=False),
                  S("https://www.barandbench.com/news/supreme-court-orders-cbi-probe-into-digital-arrest-scams", "Bar & Bench", "news", "2025-12-01", ["outcome"], fetched=False, note="cited by Wikipedia")]),
    dict(id="IN-2025-009", record_type="statistic", date="2025", date_precision="year",
         country="IN", region="national", scam_type="spam_and_fraud_calls",
         claimed_identity=[], channel=["phone"], ai_voice="not_applicable", victim_profile=None,
         loss=None, currency=None, recovered=None,
         outcome="about 31.09 lakh complaints against spam calls (including digital-arrest attempts) and about 33,000 numbers blocked in 2025, per TRAI",
         tactics=[], summary="TRAI figures for 2025 as reported in April 2026.",
         sources=[S("https://keralakaumudi.com/en/news/news-amp.php?id=1730287", "Kerala Kaumudi", "news", "2026-04-12", ["outcome"])]),
    # ------------------------------------------------------------------ United States
    dict(id="US-2025-001", record_type="campaign", date="2025-05-15", date_precision="day",
         country="US", region="national", scam_type="official_impersonation_ai_voice",
         claimed_identity=["senior US federal or state officials"], channel=["sms", "voice_message"],
         ai_voice="confirmed_by_authority", victim_profile="current and former officials and their contacts",
         loss=None, currency=None, recovered=None, outcome="FBI public service announcement I-051525-PSA",
         tactics=["authority", "rapport", "platform_switch_link", "credential_theft"],
         summary="Since April 2025, actors sent texts and AI-generated voice messages posing as senior US officials, built rapport, then sent a link to 'move to another platform' that stole account credentials.",
         sources=[S("https://www.ic3.gov/PSA/2025/PSA250515", "FBI IC3", "official", "2025-05-15", ["date", "claimed_identity", "channel", "ai_voice", "tactics"]),
                  S("https://www.aha.org/news/headline/2025-05-19-fbi-warns-malicious-text-ai-voice-messaging-campaign-impersonating-senior-us-officials", "American Hospital Association", "news", "2025-05-19", ["date", "channel", "ai_voice"])]),
    dict(id="US-2025-002", record_type="incident", date="2025-05-29", date_precision="report",
         country="US", region="national", scam_type="official_impersonation_ai_voice",
         claimed_identity=["White House Chief of Staff Susie Wiles"], channel=["sms", "phone"],
         ai_voice="reported_by_media", victim_profile="senators, governors, business leaders",
         loss=None, currency=None, recovered=None, outcome="federal investigation; no arrests reported",
         tactics=["authority", "rapport"],
         summary="Someone posing as the White House chief of staff texted and called senators, governors and business leaders; some calls reportedly used an AI imitation of her voice.",
         sources=[S("https://www.aljazeera.com/news/2025/5/30/us-government-investigates-attempt-to-impersonate-trumps-chief-of-staff", "Al Jazeera (AP)", "wire", "2025-05-30", ["date", "claimed_identity", "channel", "ai_voice", "outcome"]),
                  S("https://www.wsls.com/news/politics/2025/05/30/us-government-is-investigating-messages-impersonating-trumps-chief-of-staff-susie-wiles/", "WSLS (AP)", "wire", "2025-05-30", ["date", "claimed_identity"], fetched=False, note="same AP copy; original report by the Wall Street Journal")]),
    dict(id="US-2025-003", record_type="incident", date="2025-06", date_precision="month",
         country="US", region="international", scam_type="official_impersonation_ai_voice",
         claimed_identity=["US Secretary of State Marco Rubio"], channel=["signal_voicemail", "signal_text"],
         ai_voice="confirmed_by_authority", victim_profile="3 foreign ministers, a US governor, a member of Congress",
         loss=None, currency=None, recovered=None, outcome="State Department cable of 3 July 2025; Diplomatic Security investigating",
         tactics=["authority", "platform_switch", "information_or_account_access"],
         summary="An impostor with a Signal account named 'Marco.Rubio@state.gov' left AI-generated voicemails imitating the Secretary of State for foreign ministers and US officials, apparently to gain access to information or accounts.",
         sources=[S("https://www.adn.com/nation-world/2025/07/08/a-marco-rubio-impostor-is-using-ai-voice-to-call-high-level-officials/", "Anchorage Daily News (AP)", "wire", "2025-07-08", ["date", "claimed_identity", "channel", "ai_voice", "victim_profile", "outcome"]),
                  S("https://www.wuwf.org/2025-07-10/state-department-investigating-incident-in-which-ai-used-to-impersonate-marco-rubio", "WUWF (NPR)", "news", "2025-07-10", ["date", "channel", "ai_voice", "outcome"])]),
    dict(id="US-2025-004", record_type="incident", date="2025-07-09", date_precision="week",
         country="US", region="Dover, Florida", scam_type="family_emergency_voice_clone",
         claimed_identity=["victim's daughter (cloned voice)", "public defender / attorney", "legal courier"],
         channel=["phone", "in_person_cash_pickup"], ai_voice="alleged_by_victim", victim_profile="mother of an adult daughter",
         loss=15_000, currency="USD", recovered=None, outcome="Hillsborough County detectives investigating; second demand of $30,000 refused",
         tactics=["family_in_danger", "car_crash_pregnant_woman", "phone_confiscated", "bail", "do_not_tell_bank", "cash_courier", "second_demand"],
         summary="A sobbing voice like her daughter's said she had hit a pregnant woman while texting and police had taken her phone; a fake public defender demanded $15,000 bail in cash, collected by a courier, then $30,000 more after claiming the baby had died.",
         sources=[S("https://fox13news.com/news/dover-woman-loses-15k-after-scammers-used-artificial-intelligence-impersonate-daughter", "FOX 13 Tampa Bay", "news", "2025-07-17", ["date", "region", "claimed_identity", "channel", "loss", "outcome", "tactics"]),
                  S("https://www.malwarebytes.com/?p=279428", "Malwarebytes", "news", "2025-07-22", ["claimed_identity", "loss", "tactics"], note="cites WFLA")]),
    dict(id="US-2025-005", record_type="incident", date="2025-09-24", date_precision="report",
         country="US", region="Houston, Texas", scam_type="family_emergency_voice_clone",
         claimed_identity=["victim's son (cloned voice)", "Harris County public defender's office"],
         channel=["phone", "crypto_kiosk"], ai_voice="alleged_by_victim", victim_profile="71-year-old man",
         loss=15_000, currency="USD", recovered=None, outcome="reported to Precinct 4 Constable; calls untraceable",
         tactics=["family_in_danger", "dui_crash_pregnant_woman", "bail", "crypto_kiosk_payment", "second_demand"],
         summary="A voice like his son's said he had been arrested for DUI after hitting a pregnant woman; a fake public defender set a $15,000 bond payable at cryptocurrency kiosks, paid in four deposits.",
         sources=[S("https://www.fox26houston.com/news/houston-ai-scam-voice-clone", "FOX 26 Houston", "news", "2025-09-24", ["date", "region", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="US-2025-006", record_type="statistic", date="2025", date_precision="year",
         country="US", region="national", scam_type="imposter_scams",
         claimed_identity=[], channel=[], ai_voice="not_applicable", victim_profile=None,
         loss=3_500_000_000, currency="USD", recovered=None,
         outcome="FTC: about $3.5 billion lost to imposter scams in 2025 (government impersonation about $920 million; business impersonation nearly $1 billion); total fraud about $16 billion",
         tactics=[], summary="FTC Consumer Sentinel figures for 2025, released 15 June 2026.",
         sources=[S("https://ftc.gov/news-events/news/press-releases/2026/06/ftc-data-show-people-reported-losing-3-point-5-billion-imposter-scams-2025", "US Federal Trade Commission", "official", "2026-06-15", ["loss", "outcome"])]),
    dict(id="US-2025-007", record_type="statistic", date="2025", date_precision="year",
         country="US", region="national", scam_type="official_impersonation",
         claimed_identity=[], channel=[], ai_voice="not_applicable", victim_profile=None,
         loss=797_000_000, currency="USD", recovered=None,
         outcome="FBI IC3 2025: about 32,500 government-impersonation complaints (17,300 in 2024), about $797 million lost; AI mentioned in 260 of them (about $7 million)",
         tactics=[], summary="FBI Internet Crime Report 2025 figures as reported on release day.",
         sources=[S("https://www.nextgov.com/cybersecurity/2026/04/government-official-impersonation-scam-complaints-doubled-2025-fbi-report-shows/412656/", "Nextgov/FCW", "news", "2026-04-06", ["loss", "outcome"])]),
    # ------------------------------------------------------------------ Canada
    dict(id="CA-2025-001", record_type="enforcement", date="2025-03-04", date_precision="day",
         country="CA", region="Montreal, Quebec (victims in 40+ US states)", scam_type="family_emergency",
         claimed_identity=["grandchild", "grandchild's attorney", "bail bondsman (cash pickup)"],
         channel=["phone", "in_person_cash_pickup", "crypto"], ai_voice="not_reported", victim_profile="elderly Americans",
         loss=21_000_000, currency="USD", recovered=None,
         outcome="25 Canadians indicted in Vermont; 23 arrested in Canada on 4 March 2025",
         tactics=["family_in_danger", "car_crash", "bail", "gag_order", "cash_courier"],
         summary="Montreal call centres told grandparents a grandchild had been arrested after a car crash, put an 'attorney' on the line, warned of a gag order, and sent a 'bail bondsman' to collect cash. About $21 million was taken from 2021 to June 2024.",
         note="Calls predate 2025; included as the 2025 enforcement record of a script used in 2025 cases. The AI Incident Database links an allegation of AI voice cloning; the indictment coverage read here does not mention it.",
         sources=[S("https://www.boston.com/news/local-news/2025/03/04/25-canadians-charged-in-vermont-in-connection-with-grandparent-scam-targeting-elderly-americans/", "Boston.com", "news", "2025-03-04", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"]),
                  S("https://incidentdatabase.ai/reports/4926", "AI Incident Database (report 4926, incident 973)", "database", "2025-03-04", ["date", "outcome", "tactics"])]),
    dict(id="CA-2025-002", record_type="incident", date="2025-03-20", date_precision="report",
         country="CA", region="Oshawa, Ontario", scam_type="family_emergency_voice_clone",
         claimed_identity=["police", "victim's grandson (voice, AI suspected)"], channel=["phone"],
         ai_voice="alleged_by_victim", victim_profile="senior woman",
         loss=0, currency="CAD", recovered=None, outcome="bank staff stopped the $9,000 withdrawal; no loss",
         tactics=["family_in_danger", "arrest", "authority"],
         summary="A 'police' caller and a voice like her grandson's said he had been arrested for stealing a car and needed $9,000 for release; bank staff flagged the withdrawal.",
         sources=[S("https://www.cbc.ca/lite/story/1.7486437", "CBC News (Marketplace)", "news", "2025-03-20", ["date", "region", "claimed_identity", "ai_voice", "loss", "outcome", "tactics"])]),
    # ------------------------------------------------------------------ Italy
    dict(id="IT-2025-001", record_type="incident", date="2025-02-10", date_precision="report",
         country="IT", region="Milan / Rome", scam_type="official_impersonation_ai_voice",
         claimed_identity=["Defence Minister Guido Crosetto (cloned voice)", "ministry staff"],
         channel=["phone"], ai_voice="reported_by_media", victim_profile="prominent business leaders",
         loss=1_000_000, currency="EUR", recovered=None, outcome="four complaints to Milan prosecutors; prosecutors confirmed two transfers totalling EUR 1 million to Hong Kong",
         tactics=["authority", "patriotic_cause", "kidnapped_journalists_ransom", "promise_of_reimbursement", "money_transfer"],
         summary="Callers using a cloned voice of the defence minister and posing as his staff asked business leaders to fund the release of Italian journalists kidnapped in the Middle East, saying the Bank of Italy would reimburse them. One sent EUR 1 million to a Hong Kong account.",
         sources=[S("https://incidentdatabase.ai/reports/4719", "AI Incident Database (report 4719, incident 927)", "database", "2025-02-10", ["date", "claimed_identity", "loss", "outcome", "tactics"]),
                  S("https://hotnews.ro/ministrul-italian-al-apararii-dezvaluie-ca-giorgio-armani-si-alti-afaceristi-cunoscuti-au-fost-tintiti-de-o-escrocherie-complexa-1899393", "HotNews.ro", "news", "2025-02-11", ["date", "claimed_identity", "ai_voice", "loss", "outcome", "tactics"]),
                  S("https://www.theguardian.com/world/2025/feb/10/ai-phone-scam-targets-italian-business-leaders-including-giorgio-armani", "The Guardian", "news", "2025-02-10", ["date", "claimed_identity", "loss"], fetched=False, note="original report; site blocked our reader")]),
    # ------------------------------------------------------------------ Singapore (police advisories)
    dict(id="SG-2025-001", record_type="campaign", date="2025-01-22", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["telecom staff (Singtel, StarHub)", "overseas law enforcement officer"], channel=["phone", "whatsapp"],
         ai_voice="not_reported", victim_profile="elderly victims", loss=5_900_000, currency="SGD", recovered=None,
         outcome="SPF advisory; two December 2024 cases lost $1 million and $4.9 million",
         tactics=["number_used_in_crime", "call_transfer", "fake_police_id", "cash_safekeeping", "cash_courier"],
         summary="A 'telco' caller said the victim's number was used for crime overseas and transferred the call to a fake overseas police officer, who told the victim to withdraw cash and hand it to a stranger for 'safekeeping'.",
         sources=[S("https://police.gov.sg/Media-Room/News/20250122_police_advisory_on_government_official_impersonation_scams", "Singapore Police Force", "official", "2025-01-22", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="SG-2025-002", record_type="campaign", date="2025-06-03", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["Ministry of Manpower officer", "SPF or ICA official"], channel=["phone"],
         ai_voice="not_reported", victim_profile="employers", loss=379_000, currency="SGD", recovered=None,
         outcome="SPF advisory; at least 6 cases since May 2025",
         tactics=["workers_in_crime", "call_transfer", "money_laundering_accusation", "asset_declaration", "secrecy", "daily_reporting", "cash_safekeeping"],
         summary="A fake MOM officer said workers employed under the victim's details were involved in crime, then transferred the call to a fake police or ICA official who demanded money 'for investigation', asset declarations, daily reporting and secrecy.",
         sources=[S("https://www.police.gov.sg/media-room/news/20250603_police_advisory_on_scammers_impersonating_officers_from_the_ministry_of_manpower_mom", "Singapore Police Force", "official", "2025-06-03", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="SG-2025-003", record_type="campaign", date="2025-08-22", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["bank staff (Maybank, Citibank)", "insurer or payment firm", "MAS or SPF official"],
         channel=["whatsapp_call", "whatsapp_video"], ai_voice="not_reported", victim_profile="general public",
         loss=4_100_000, currency="SGD", recovered=None, outcome="SPF advisory; at least 61 cases since July 2025",
         tactics=["card_in_your_name", "call_transfer", "money_laundering_accusation", "safety_account", "crypto_safety_wallet", "cash_courier"],
         summary="WhatsApp callers posing as a bank or insurer claimed card transactions or unpaid premiums, then transferred the call to a fake MAS or police official who accused the victim of money laundering and had them move money to a 'safety account' or buy USDC for a 'safety wallet'.",
         sources=[S("https://www.police.gov.sg/Media-Room/News/20250822_police_advisory_on_government_official_impersonation_scams_involving_whatsapp_calls", "Singapore Police Force", "official", "2025-08-22", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="SG-2025-004", record_type="campaign", date="2025-09-01", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["bank or insurer (DBS, Maybank, Income, UnionPay)", "ICA or MOH", "MAS, SPF, MinLaw, China or Malaysia police"],
         channel=["phone"], ai_voice="not_reported", victim_profile="general public",
         loss=20_200_000, currency="SGD", recovered=None, outcome="SPF advisory; at least 384 cases since June 2025",
         tactics=["unpaid_premiums", "fraudulent_claims", "call_transfer", "money_laundering_accusation", "buy_gold", "valuables_handover", "blank_cheques"],
         summary="Callers posing as a bank, insurer or agency alleged unpaid premiums, false claims or suspicious transactions; a second 'official' accused the victim of money laundering and had them withdraw cash, buy gold or hand over valuables and pre-signed cheques 'for investigation'.",
         sources=[S("https://www.police.gov.sg/Media-Room/News/20250901_police_advisory_on_government_officials_impersonation_scams", "Singapore Police Force", "official", "2025-09-01", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="SG-2025-005", record_type="campaign", date="2025-10-06", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["telco staff (M1)", "fake M1 customer service", "MAS officer"],
         channel=["phone", "whatsapp_call", "screen_share"], ai_voice="not_reported", victim_profile="general public",
         loss=362_000, currency="SGD", recovered=None, outcome="SPF advisory; at least 13 cases since September 2025",
         tactics=["unpaid_plan", "call_transfer", "identity_misuse", "safety_account", "screen_share_banking", "cash_courier"],
         summary="A fake M1 caller claimed unpaid mobile plans, sent the victim to a fake WhatsApp 'customer service', which passed them to a fake MAS officer who alleged their particulars were used for money laundering and had them transfer money, sometimes while screen-sharing their bank login.",
         sources=[S("https://www.police.gov.sg/Media-Hub/News/2025/10/20251006_police_advisory_on_government_officials_impersonation_scams", "Singapore Police Force", "official", "2025-10-06", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
    dict(id="SG-2025-006", record_type="campaign", date="2025-11-07", date_precision="day",
         country="SG", region="national", scam_type="official_impersonation",
         claimed_identity=["bank representative", "Ministry of Home Affairs officer"], channel=["phone"],
         ai_voice="not_reported", victim_profile="general public", loss=52_000, currency="SGD", recovered=None,
         outcome="SPF advisory; at least 7 cases since September 2025",
         tactics=["card_in_your_name", "call_transfer", "money_laundering_accusation", "fake_warrant", "gold_handover", "crypto"],
         summary="A 'bank' caller reported a credit card in the victim's name and transferred them to a fake MHA officer who showed a fake arrest warrant and court notice and demanded PayNow transfers, gold or crypto 'for investigation'.",
         sources=[S("https://www.police.gov.sg/media-hub/news/2025/11/20251107_police_advisory_on_government_official_impersonation_scams_involving_officers", "Singapore Police Force", "official", "2025-11-07", ["date", "claimed_identity", "channel", "loss", "outcome", "tactics"])]),
]

EXCLUDED = [
    dict(candidate="Mumbai 69-year-old, Rs 50 lakh, suspected AI-cloned voice of a community figure", reason="outside window (Sept 2026)", url="https://the420.in/mumbai-senior-citizen-50-lakh-ai-voice-cloning-scam/"),
    dict(candidate="Delhi 65-year-old, Rs 2 lakh, daughter's cloned voice, fake kidnapping", reason="outside window (Feb 2026) and no police confirmation", url="https://the420.in/ai-voice-clone-scam-delhi-elderly-woman-kidnapping-fraud/"),
    dict(candidate="Indore play-school owner, Rs 97,500, cousin's voice, police suspect AI", reason="outside window (Jan 2026)", url="https://the420.in/ai-voice-cloning-fraud-madhya-pradesh-indore-play-school-owner-duped/"),
    dict(candidate="Rajasthan Police AI voice-cloning alert", reason="outside window (May 2026)", url="https://www.patrika.com/jaipur-news/rajasthan-police-issues-major-alert-against-ai-voice-cloning-scam-20626816"),
    dict(candidate="Rajasthan retired judge, Rs 2.5 crore digital arrest", reason="outside window (Aug 2026)", url="https://the420.in/rajasthan-retired-judge-digital-arrest-fraud-kerala-suspect"),
    dict(candidate="CBI Operation Chakra-V arrests in Rs 7.67 crore case", reason="calls in 2023-24; 2025 record is arrests only", url="https://www.theweek.in/wire-updates/national/2025/05/05/del72-cbi-digital-arrest.html"),
    dict(candidate="Harris County couple, son's voice, $5,000", reason="no date in the source", url="https://khou.com/article/news/local/phone-voice-clone-scam-houston-area-crime/285-999031eb-d3c8-41db-8d0a-0ccf05273194"),
    dict(candidate="Osceola County sheriff's cloned voice in voicemails", reason="outside window (Oct 2026); no date of calls", url="https://www.404media.co/police-say-scammers-used-ai-to-clone-a-sheriffs-voice/"),
    dict(candidate="Gurugram 70-year-old, Rs 78.9 lakh; Pune and Jabalpur cases", reason="only reachable via one blocked outlet; not read", url="https://www.hindustantimes.com/cities/gurugram-news/70yrold-woman-loses-78-9l-in-gurugram-digital-arrest-scam-101764537930368.html"),
    dict(candidate="PDPC officer impersonation advisory (SG, Feb 2025)", reason="page body unavailable; no pattern details", url="https://www.pdpc.gov.sg/news-and-events/press-room/2025/02/advisory-on-government-official-impersonation-scams-involving-impersonation-of-personal-data-protection-commission-officers"),
]


def level(rec) -> str:
    """A: a primary official source was read. B: two or more independent sources were read and agree.
    C: one source was read. Wire copies of the same story count once."""
    read = [s for s in rec["sources"] if s["fetched"]]
    if any(s["source_type"] == "official" for s in read):
        return "A"
    outlets = {s["publisher"].split(" (")[1].rstrip(")") if s["source_type"] == "wire" and " (" in s["publisher"] else s["publisher"] for s in read}
    if len(outlets) >= 2:
        return "B"
    return "C" if read else "unverified"


def main() -> None:
    rows = []
    with open(HERE / "incidents.jsonl", "w", encoding="utf-8") as f:
        for rec in INCIDENTS:
            rec = dict(rec)
            rec.setdefault("note", "")
            rec["verification_level"] = level(rec)
            rec["loss_usd_approx"] = round(rec["loss"] * USD[rec["currency"]]) if rec.get("loss") and rec.get("currency") in USD else None
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for s in rec["sources"]:
                rows.append({"incident_id": rec["id"], **{k: (";".join(v) if isinstance(v, list) else v) for k, v in s.items()}})
    with open(HERE / "sources.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (HERE / "excluded.json").write_text(json.dumps(EXCLUDED, indent=1, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(len(INCIDENTS), "records", Counter(level(r) for r in INCIDENTS), Counter(r["record_type"] for r in INCIDENTS))


if __name__ == "__main__":
    main()
