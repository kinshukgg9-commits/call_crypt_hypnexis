"""Source of calls.jsonl: reconstructed call scripts for the test set.

Scam calls (label 1) are reconstructions of the documented 2025 cases in incidents_src.py. They are
written from the facts the sources report: who the caller claimed to be, the story, and what was
asked. They are paraphrases, not transcripts: no recording of these calls is public. Lines the
sources describe directly are marked "reported"; connecting lines a caller would need to say are
marked "filler". Genuine calls (label 0) are written to look like scams on the surface: the same
institutions, money, police, urgency. They are there to measure false alarms.

These lines were written for this set and not tuned against backend/scam_rules.py, so the
benchmark measures the rules rather than restating them. Run check_overlap.py before release.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def C(id, incident, lang, turns, scam_type, label=1, note=""):
    return dict(id=id, incident_id=incident, label=label, language=lang, scam_type=scam_type,
                turns=[dict(speaker=s, text=t, basis=b) for s, t, b in turns], note=note)


R, F = "reported", "filler"
c, v = "caller", "callee"

SCAM = [
    # ---------------------------------------------------------------- India (English)
    C("call-001", "IN-2025-001", "en", [
        (c, "Hello, I am calling from the Delhi cyber cell about a serious case registered on your mobile number.", F),
        (v, "What case? We are old people, we have done nothing.", F),
        (c, "Your number and bank account have been used in an illegal transaction. Until the inquiry is over you and your wife are under digital arrest.", R),
        (c, "You will not go out of the house and you will not discuss this with your relatives. If you speak to anyone, the local police will pick you up.", R),
        (c, "To prove the money is clean, move your savings to the account we give you. It will be returned after verification.", R),
        (v, "All our savings? That is fifty lakh.", R),
        (c, "Yes, transfer the full amount today itself. This is an order from the investigating officer.", R),
    ], "digital_arrest"),
    C("call-002", "IN-2025-002", "en", [
        (c, "This is an automated alert. Your SIM card will be blocked within two hours because of complaints against your number. Press nine to speak to TRAI.", R),
        (c, "I am transferring your call to the Mumbai cyber crime branch, please hold.", R),
        (c, "I am an IPS officer. Your name has come up in a money laundering racket. Twenty four cases have been registered against you in different states.", R),
        (v, "Sir, there must be some mistake.", F),
        (c, "Your wife and daughter are also part of this investigation. All three of you are under digital arrest. Nobody leaves the house, nobody calls anybody.", R),
        (c, "Keep the video on. If you want to avoid jail, deposit the amount for verification in the account I am sending. It will be refunded once you are cleared.", R),
    ], "digital_arrest"),
    C("call-003", "IN-2025-003", "en", [
        (c, "Good morning, I am Deepak Sharma from the Telecom Regulatory Authority. A money laundering case has been registered in Mumbai under your Aadhaar number.", R),
        (c, "I will connect you to the Mumbai Police sub-inspector handling your file.", R),
        (c, "Sir, the CBI chief himself is monitoring this. You are in virtual custody from now. Keep your WhatsApp video on at all times.", R),
        (c, "The Supreme Court will hear your matter online. The judge has ordered that your fixed deposits and PPF be verified.", R),
        (v, "Do I have to break my FDs?", F),
        (c, "Yes. Liquidate them and send the funds to the account under Supreme Court supervision. If you contact anyone, the custody becomes physical.", R),
    ], "digital_arrest"),
    C("call-004", "IN-2025-004", "en", [
        (c, "Namaste, this is TRAI. Your mobile number is linked to illegal activity and will be disconnected.", R),
        (c, "The Maharashtra Police want to speak to you. Stay on the line.", R),
        (c, "Uncle, this is a police matter. There is a case against you and an arrest warrant can be issued today.", R),
        (v, "I am ninety two years old, what have I done?", F),
        (c, "If you are innocent, cooperate. Transfer your money to the government account for checking and we will close the case.", R),
    ], "digital_arrest"),
    C("call-005", "IN-2025-005", "en", [
        (c, "Sir, this call is from TRAI. A SIM issued on your documents is being used for fraud. We are connecting you to the Mumbai crime branch.", R),
        (c, "Crime branch here. Your identity is linked to a money laundering case worth crores. You must join a video call for interrogation now.", R),
        (c, "The court is in session. The honourable judge will now hear your case.", R),
        (c, "As per the court's order, you will sell your shares and investments and transfer the proceeds for verification. You will receive everything back after the inquiry.", R),
        (v, "How long will this take?", F),
        (c, "About forty days. Report to us every day and do not discuss this with your family or your bank.", R),
    ], "digital_arrest"),
    C("call-006", "IN-2025-006", "en", [
        (c, "Hello madam, I am calling from DHL, Andheri. A parcel in your name has been held. It contains three credit cards, four passports and MDMA.", R),
        (v, "I have not sent any parcel.", F),
        (c, "Your phone number is on the parcel, so it is a cyber crime matter. I am transferring you to the CBI.", R),
        (c, "This is Pradeep Singh, CBI. Your identity has been misused. Do not go to the local police, the criminals are watching them. You are under digital arrest.", R),
        (c, "Keep Skype open with the camera on. An officer will monitor you for a week.", R),
        (c, "To prove your innocence you must declare all your assets to the RBI's Financial Intelligence Unit. Transfer them for verification; they will be returned with a clearance certificate.", R),
    ], "digital_arrest"),
    C("call-007", "IN-2025-007", "en", [
        (c, "This is the CBI. Your Aadhaar was used to open an account in the Canara Bank branch in Mumbai, and that account is used for illegal activity.", R),
        (c, "I am showing you the FIR from the Delhi Crime Branch on the video. Look at it.", R),
        (v, "That account is not mine.", F),
        (c, "Then you must clear your name. Deposit your money in the accounts we give you so the department can verify it.", R),
        (c, "Do it today. Until the money is checked, you are a suspect.", F),
    ], "digital_arrest"),
    # ---------------------------------------------------------------- India (Hinglish, romanized)
    C("call-008", "IN-2025-001", "hi-Latn", [
        (c, "Hello, main Delhi cyber cell se bol raha hoon, aapke mobile number par ek serious case darj hua hai.", F),
        (c, "Aapka account illegal transaction mein use hua hai. Jab tak inquiry chal rahi hai, aap aur aapki wife digital arrest mein hain.", R),
        (c, "Ghar se bahar nahi jaana, rishtedaron se baat nahi karni. Kisi ko bataya to local police utha legi.", R),
        (c, "Apni saari savings hamare diye hue account mein daal dijiye, verification ke baad wapas ho jayegi.", R),
    ], "digital_arrest"),
    C("call-009", "IN-2025-002", "hi-Latn", [
        (c, "Aapka SIM do ghante mein band ho jayega, aapke number par complaint aayi hai. TRAI se baat karne ke liye nau dabaiye.", R),
        (c, "Main IPS officer bol raha hoon. Aapke khilaf money laundering ke chaubis case darj hain.", R),
        (c, "Aapki wife aur beti bhi investigation mein hain. Teeno digital arrest mein ho, koi ghar se nahi niklega, kisi ko phone nahi karega.", R),
        (c, "Jail se bachna hai to verification ke liye amount is account mein jama karo, clear hone par refund ho jayega.", R),
    ], "digital_arrest"),
    C("call-010", "IN-2025-003", "hi-Latn", [
        (c, "Main TRAI se Deepak Sharma bol raha hoon. Aapke Aadhaar par Mumbai mein money laundering ka case bana hai.", R),
        (c, "Aap ab virtual custody mein ho. WhatsApp video band mat karna, CBI chief khud dekh rahe hain.", R),
        (c, "Supreme Court ke judge ka order hai, apni FD aur PPF tod kar paisa court supervision wale account mein bhejo.", R),
    ], "digital_arrest"),
    C("call-011", "IN-2025-004", "hi-Latn", [
        (c, "Namaste, TRAI se call hai. Aapka number illegal activity se juda hai, connection kaat diya jayega.", R),
        (c, "Maharashtra Police aapse baat karegi, line par bane rahiye.", R),
        (c, "Uncle aapke naam par case hai, aaj hi warrant nikal sakta hai. Paise checking ke liye sarkari account mein transfer kar dijiye, case band ho jayega.", R),
    ], "digital_arrest"),
    C("call-012", "IN-2025-005", "hi-Latn", [
        (c, "Sir, TRAI se bol rahe hain. Aapke documents par liya SIM fraud mein use ho raha hai, Mumbai crime branch se connect kar rahe hain.", R),
        (c, "Crime branch. Aapki identity crore rupaye ke money laundering case se judi hai, abhi video call par interrogation hoga.", R),
        (c, "Court ka order hai, apne shares bech kar paisa verification ke liye bhejiye. Inquiry ke baad sab wapas milega.", R),
        (c, "Roz report karna, family ya bank ko kuch nahi batana.", R),
    ], "digital_arrest"),
    C("call-013", "IN-2025-006", "hi-Latn", [
        (c, "Madam, DHL Andheri se bol raha hoon. Aapke naam ke parcel mein teen credit card, chaar passport aur MDMA mila hai.", R),
        (c, "Main CBI se Pradeep Singh. Local police ke paas mat jaana. Aap digital arrest mein ho, Skype camera on rakho.", R),
        (c, "Begunah ho to apni saari property RBI ki Financial Intelligence Unit ko declare karo aur verification ke liye transfer karo.", R),
    ], "digital_arrest"),
    C("call-014", "IN-2025-007", "hi-Latn", [
        (c, "CBI se bol rahe hain. Aapke Aadhaar se Mumbai ki Canara Bank branch mein account khula hai jo illegal kaam mein use ho raha hai.", R),
        (c, "Video par Delhi Crime Branch ki FIR dekhiye.", R),
        (c, "Naam clear karna hai to apna paisa hamare diye accounts mein jama kijiye, department verify karega.", R),
    ], "digital_arrest"),
    # ---------------------------------------------------------------- India (Hindi, Devanagari)
    C("call-015", "IN-2025-002", "hi", [
        (c, "आपका सिम दो घंटे में बंद कर दिया जाएगा। ट्राई से बात करने के लिए नौ दबाइए।", R),
        (c, "मैं आईपीएस अधिकारी बोल रहा हूँ। आपके खिलाफ मनी लॉन्ड्रिंग के चौबीस केस दर्ज हैं।", R),
        (c, "आपकी पत्नी और बेटी भी डिजिटल अरेस्ट में हैं। कोई घर से बाहर नहीं जाएगा।", R),
        (c, "जेल से बचना है तो वेरिफिकेशन के लिए रकम इस खाते में जमा कीजिए।", R),
    ], "digital_arrest"),
    C("call-016", "IN-2025-005", "hi", [
        (c, "सर, ट्राई से बोल रहे हैं। आपके दस्तावेज़ों पर लिया सिम धोखाधड़ी में इस्तेमाल हो रहा है।", R),
        (c, "क्राइम ब्रांच मुंबई। आपकी पहचान करोड़ों के मनी लॉन्ड्रिंग केस से जुड़ी है। अभी वीडियो कॉल पर पूछताछ होगी।", R),
        (c, "अदालत का आदेश है, अपने शेयर बेचकर पैसा जांच के लिए भेजिए। परिवार या बैंक को कुछ मत बताइए।", R),
    ], "digital_arrest"),
    C("call-017", "IN-2025-007", "hi", [
        (c, "सीबीआई से बोल रहे हैं। आपके आधार से मुंबई में केनरा बैंक का खाता खुला है जो गैरकानूनी काम में लगा है।", R),
        (c, "वीडियो पर दिल्ली क्राइम ब्रांच की एफआईआर देखिए।", R),
        (c, "अपना नाम साफ़ करना है तो अपना पैसा हमारे बताए खातों में जमा कीजिए।", R),
    ], "digital_arrest"),
    # ---------------------------------------------------------------- United States
    C("call-018", "US-2025-001", "en", [
        (c, "Hi, it's me. I'm sending this from my personal line, things are hectic in Washington this week.", F),
        (c, "We should keep talking, but not over text. Let's move this to a more secure platform.", R),
        (c, "I'll send you a link now. Just sign in with your usual account so we're connected there.", R),
    ], "official_impersonation_ai_voice", note="AI-generated voice message; the ask is a credential-stealing link"),
    C("call-019", "US-2025-002", "en", [
        (c, "Governor, this is Susie. I'm reaching out on a different number for a moment.", R),
        (c, "The President asked me to put together a short list of people to work with, and I wanted you on it.", F),
        (c, "Can you send me a few names and the best way to reach them? Keep this between us for now.", F),
    ], "official_impersonation_ai_voice", note="Sources report texts and AI-voiced calls but not the exact ask; the ask lines are filler"),
    C("call-020", "US-2025-003", "en", [
        (c, "Minister, this is Marco Rubio. I wanted to leave you a quick message.", R),
        (c, "I'd like to continue our conversation on Signal. It's the easiest way for me to reach you directly.", R),
        (c, "Please reply when you get this and we'll set up a time.", F),
    ], "official_impersonation_ai_voice", note="AI-generated voicemail on Signal"),
    C("call-021", "US-2025-004", "en", [
        (c, "Mom? Mom, it's me. I'm so sorry. I was in an accident. I was texting and I hit a pregnant woman.", R),
        (c, "The police took my phone. Please, please help me.", R),
        (c, "Ma'am, I'm the public defender assigned to your daughter. She's being detained. Bail is fifteen thousand dollars and it has to be paid today, in cash.", R),
        (c, "Don't tell the bank why you're withdrawing it. It could affect your daughter's credit and the case.", R),
        (c, "A courier from our office will come to your home to collect it.", R),
        (c, "I'm sorry to tell you the woman lost the baby. The family will agree not to sue if you pay another thirty thousand.", R),
    ], "family_emergency_voice_clone"),
    C("call-022", "US-2025-005", "en", [
        (c, "Dad, it's me. I messed up. I got arrested for DUI, I hit a pregnant woman.", R),
        (c, "Sir, I'm with the Harris County Public Defender's Office. Your son's bond is fifteen thousand dollars.", R),
        (c, "We've arranged to bypass the DA's office and the courthouse so this goes quickly. You'll pay at a cryptocurrency kiosk; I'll stay on the line and walk you through it.", R),
        (c, "There's an additional amount needed before he can be released.", R),
    ], "family_emergency_voice_clone"),
    # ---------------------------------------------------------------- Canada
    C("call-023", "CA-2025-001", "en", [
        (c, "Grandma? It's me. I've been in a car accident and they arrested me.", R),
        (c, "Ma'am, I'm your grandson's attorney. There is a gag order on this case; you can't tell anyone, not even his parents.", R),
        (c, "If we post bail today, the charges can be dropped. A bail bondsman will come to your house to pick up the cash.", R),
    ], "family_emergency"),
    C("call-024", "CA-2025-002", "en", [
        (c, "This is the police. Your grandson is in custody.", R),
        (c, "Grandma, it's me. They arrested me for stealing a car. I need nine thousand dollars to get out. Please don't tell Dad.", R),
    ], "family_emergency_voice_clone"),
    # ---------------------------------------------------------------- Italy
    C("call-025", "IT-2025-001", "en", [
        (c, "Good afternoon, this is Guido Crosetto, the Minister of Defence. I am calling on a very delicate matter.", R),
        (c, "Italian journalists have been kidnapped in the Middle East. The state cannot pay directly, so we are asking a few trusted patriots for help.", R),
        (c, "You would transfer the funds to an account abroad. The Bank of Italy will reimburse you in full.", R),
        (c, "Please keep this confidential. Lives depend on it.", F),
    ], "official_impersonation_ai_voice", note="Calls were in Italian; English reconstruction"),
    # ---------------------------------------------------------------- Singapore
    C("call-026", "SG-2025-001", "en", [
        (c, "Hello, this is Singtel. Your mobile number has been used for criminal activity overseas.", R),
        (c, "I will transfer you to the officer handling the case.", R),
        (c, "I am a police officer. I am sending my warrant card to your WhatsApp now; it will disappear after you view it.", R),
        (c, "For safekeeping during the investigation, withdraw the money in your bank account. Our colleague will meet you to collect it.", R),
    ], "official_impersonation"),
    C("call-027", "SG-2025-002", "en", [
        (c, "This is the Ministry of Manpower. Workers employed under your details are involved in immigration offences.", R),
        (v, "I don't employ any workers.", R),
        (c, "Then I will transfer you to the police officer on this case.", R),
        (c, "We suspect you are involved in money laundering. Declare your financial assets for checks, transfer the money to us for investigation, and report to me every day. This investigation is confidential.", R),
    ], "official_impersonation"),
    C("call-028", "SG-2025-003", "en", [
        (c, "Hello, this is Maybank. There are transactions on a credit card applied for in your name.", R),
        (v, "I never applied for that card.", R),
        (c, "Then I need to transfer you to the Monetary Authority of Singapore.", R),
        (c, "MAS here. Your accounts are linked to money laundering. Move your funds to a safety account while we investigate, or buy USDC and send it to the safety wallet I give you.", R),
    ], "official_impersonation"),
    C("call-029", "SG-2025-004", "en", [
        (c, "This is Income Insurance. You have unpaid premiums and a medical claim filed in your name that looks fraudulent.", R),
        (c, "I am transferring you to the police.", R),
        (c, "You are a suspect in money laundering. Withdraw your cash, buy gold bars and declare your jewellery and watches. An undercover officer will collect them for investigation.", R),
        (c, "You will also sign some blank cheques for us.", R),
    ], "official_impersonation"),
    C("call-030", "SG-2025-005", "en", [
        (c, "Hi, this is M1. You have an outstanding payment on your mobile plan and a phone delivery pending.", R),
        (v, "I don't have a plan with M1.", R),
        (c, "Please message our customer service on WhatsApp to terminate it.", R),
        (c, "This is an officer of the Monetary Authority of Singapore. Your particulars were used for money laundering. Share your screen while you log in to your bank, and transfer your balance to the safety account designated by the government.", R),
    ], "official_impersonation"),
    C("call-031", "SG-2025-006", "en", [
        (c, "Good morning, I'm calling from your bank. A credit card was found under your name with suspicious transactions.", R),
        (c, "I'll connect you to an officer from the Ministry of Home Affairs.", R),
        (c, "You are involved in money laundering. I am sending a warrant of arrest and a court hearing notice. For investigation purposes, make a PayNow transfer, scan this QR code, or hand over your gold.", R),
    ], "official_impersonation"),
]

GENUINE = [
    C("call-101", None, "en", [
        ("caller", "Hello, this is the fraud prevention team at your bank. We blocked a card payment of four thousand nine hundred rupees at an online store a few minutes ago.", F),
        ("caller", "If you made this payment, you can approve it in the app. If not, your card stays blocked and a new one will be posted to you.", F),
        ("caller", "We will never ask you for your PIN or a one-time password on a call.", F),
    ], "bank_fraud_alert", label=0),
    C("call-102", None, "en", [
        ("caller", "Hi, this is the delivery partner. Your parcel is out for delivery and I will reach in about twenty minutes.", F),
        ("caller", "It's cash on delivery, four hundred ninety nine rupees. Will someone be at home?", F),
    ], "delivery", label=0),
    C("call-103", None, "en", [
        ("caller", "Mom, it's me. Don't worry, I'm fine. Someone bumped into my car at the signal.", F),
        ("caller", "The police took down the details and the insurance people are coming. I'll call you when I get home.", F),
    ], "family", label=0),
    C("call-104", None, "en", [
        ("caller", "Did you see the news about the man in Mumbai who lost fifty eight crore in a digital arrest scam?", F),
        ("caller", "They kept him on video calls for weeks pretending to be from the crime branch. If anyone ever calls you like that, just hang up and call me.", F),
    ], "conversation_about_scams", label=0),
    C("call-105", None, "en", [
        ("caller", "Good morning, I'm calling from the police station about your passport verification.", F),
        ("caller", "Please come to the station on Monday between ten and one, and bring a copy of your Aadhaar and your address proof.", F),
    ], "police_genuine", label=0),
    C("call-106", None, "en", [
        ("caller", "This is a reminder from the electricity board. Your bill of one thousand two hundred forty rupees is due on the fifteenth.", F),
        ("caller", "You can pay through our official website, the app you already use, or at the counter. We never ask customers to install any other app.", F),
    ], "utility_reminder", label=0),
    C("call-107", None, "en", [
        ("caller", "Hello sir, I'm your relationship manager at the bank. Your fixed deposit matures next Tuesday.", F),
        ("caller", "Would you like to renew it for another year, or should we credit it to your savings account?", F),
    ], "bank_service", label=0),
    C("call-108", None, "en", [
        ("caller", "Hi, this is the clinic. Just confirming your appointment with the doctor tomorrow at eleven thirty.", F),
        ("caller", "Please bring your old reports and arrive ten minutes early.", F),
    ], "appointment", label=0),
    C("call-109", None, "en", [
        ("caller", "Good afternoon, this is the school office. The second term fees are due this Friday.", F),
        ("caller", "You can pay at the office or on the parent portal. Please ignore this if you have already paid.", F),
    ], "fee_reminder", label=0),
    C("call-110", None, "en", [
        ("caller", "Hello, your health insurance policy renews at the end of this month.", F),
        ("caller", "The premium is the same as last year. You can renew on our website or I can send you a payment link by email.", F),
    ], "insurance_renewal", label=0),
    C("call-111", None, "en", [
        ("caller", "Hi, it's your tax consultant. The deadline to file your income tax return is the thirty first of July.", F),
        ("caller", "Please email me your Form 16 and the interest certificates from your bank this week.", F),
    ], "tax_consultant", label=0),
    C("call-112", None, "en", [
        ("caller", "Papa, the college sent the notice. The semester fee is forty five thousand and the last date is today.", F),
        ("caller", "Can you transfer it to the college account? I've sent you the fee receipt link from the college website.", F),
    ], "family_money", label=0, note="hard negative: urgent money request from family"),
    C("call-113", None, "en", [
        ("caller", "Hello, your prepaid plan expires tomorrow. Recharge any time to keep your number active.", F),
        ("caller", "You can recharge from the app or at any store.", F),
    ], "telecom_reminder", label=0),
    C("call-114", None, "en", [
        ("caller", "This is an awareness message from the cyber police. Never share an OTP or your bank details with anyone who calls you.", F),
        ("caller", "No police officer will ever place you under arrest on a video call. If you get such a call, report it on 1930.", F),
    ], "awareness_message", label=0, note="hard negative: names the scam and the police"),
    C("call-115", None, "en", [
        ("caller", "Hello, I'm calling from your advocate's office. Your hearing at the consumer court is on the twelfth.", F),
        ("caller", "Please bring the original bill and the warranty card. The court starts at ten thirty.", F),
    ], "lawyer_genuine", label=0),
    C("call-116", None, "en", [
        ("caller", "Grandpa, my car broke down near the mall. Could you pick me up? My phone's at five percent.", F),
        ("caller", "No need for money, I just need a ride home.", F),
    ], "family", label=0),
    C("call-117", None, "en", [
        ("caller", "The client wants the payment for the March invoice transferred by Monday.", F),
        ("caller", "Can you approve it in the finance system today? The details are in the usual vendor record.", F),
    ], "work_payment", label=0),
    C("call-118", None, "en", [
        ("caller", "Hello, I'm calling about the complaint you raised for a refund.", F),
        ("caller", "The refund of seven hundred ninety nine rupees has been approved and will reach your account in five to seven days. You don't need to do anything.", F),
    ], "customer_care_genuine", label=0),
    C("call-119", None, "en", [
        ("caller", "This is your bank. We noticed a sign-in to your account from a new device.", F),
        ("caller", "If this wasn't you, please call the number printed on the back of your card. Don't share any codes with anyone.", F),
    ], "bank_fraud_alert", label=0),
    C("call-120", None, "en", [
        ("caller", "Sir, good news, your stolen phone has been recovered.", F),
        ("caller", "Please come to the police station with the purchase bill and an ID to collect it.", F),
    ], "police_genuine", label=0),
    C("call-121", None, "hi-Latn", [
        ("caller", "Bhai kal milte hain office ke baad. Paise ka hisaab baad mein kar lenge, koi jaldi nahi hai.", F),
    ], "friend", label=0),
    C("call-122", None, "hi-Latn", [
        ("caller", "Beta khana kha liya? Papa ki tabiyat ab theek hai, doctor ne kaha aaram karna hai.", F),
    ], "family", label=0),
    C("call-123", None, "hi-Latn", [
        ("caller", "Sir aapka order aa gaya hai, gate par aa jaiye. Payment ho chuka hai.", F),
    ], "delivery", label=0),
    C("call-124", None, "hi-Latn", [
        ("caller", "Aaj news mein tha ki digital arrest naam ka scam chal raha hai. Koi CBI bol ke call kare to phone kaat dena aur 1930 par report karna.", F),
    ], "conversation_about_scams", label=0, note="hard negative: names the scam"),
    C("call-125", None, "hi", [
        ("caller", "नमस्ते, आपकी ईएमआई की तारीख पांच तारीख है। कृपया खाते में बैलेंस रखें।", F),
        ("caller", "किसी भी जानकारी के लिए बैंक की शाखा में आएं।", F),
    ], "bank_service", label=0),
    C("call-126", None, "hi", [
        ("caller", "पापा, कॉलेज की फीस आज जमा करनी है। कॉलेज की वेबसाइट पर रसीद भेज दी है।", F),
    ], "family_money", label=0, note="hard negative: urgent money request from family"),
]


def main() -> None:
    with open(HERE / "calls.jsonl", "w", encoding="utf-8") as f:
        for call in SCAM + GENUINE:
            f.write(json.dumps(call, ensure_ascii=False) + "\n")
    print(len(SCAM), "scam calls,", len(GENUINE), "genuine calls")


if __name__ == "__main__":
    main()
