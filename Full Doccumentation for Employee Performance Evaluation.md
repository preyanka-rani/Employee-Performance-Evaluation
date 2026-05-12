# **Employee Performance Evaluation Framework**

এই ডকুমেন্টটি কর্মীদের কাজের মান এবং প্রফেশনালিজম মূল্যায়নের জন্য একটি মানসম্মত scoring methodology প্রদান করে। ফাইনাল স্কোরটি ১০০ নম্বরের ওপর ভিত্তি করে ক্যালকুলেট করা হয়, যেখানে টেকনিক্যাল লগ, ডিসিপ্লিন, টিম লিড অ্যাসেসমেন্ট এবং বোনাস রিওয়ার্ড অন্তর্ভুক্ত থাকে।

## **১. For Developer Performance Evaluation**

### **১.১ Score Calculation Structure**

পুরো ইভ্যালুয়েশন প্রসেসটি দুটি প্রধান ভাগে বিভক্ত, যেখানে প্রতিটি সেগমেন্ট থেকে ৫০ মার্কস করে মোট ১০০ নম্বরের বেস স্কোর তৈরি করা হয়।

#### **Segment A: Technical Performance (৫০ Marks)**

এই অংশটি 'Developer Quality Check System' এবং টেকনিক্যাল লগ (EBS \+ Codelab) থেকে ডিরাইভ করা হয়।

* **Component 1 (Quality Check):** অটোমেটেড কোয়ালিটি চেক সিস্টেম থেকে ১০০ নম্বরের একটি বেস স্কোর নেওয়া হয়।  
* **Component 2 (Work Logs & Log Description):** EBS \+ Codelab লগ থেকে ১০০ নম্বরের একটি বেস স্কোর জেনারেট করা হয়:

**Work Logs (৯০%):** এটি লগ আওয়ারের পরিমাণ এবং প্রজেক্ট টাইপের ওপর ভিত্তি করে নির্ধারিত হয়।

\-- Work Log Extraction Query (Reference: perform\_crm.sql)  
SELECT  
u.user\_email,  
YEAR(alog.work\_dt) as year,  
MONTH(alog.work\_dt) as month\_id,  
ROUND(SUM(IFNULL(TIME\_TO\_SEC(CONCAT(alog.work\_duation,':00'))/3600,0)),2) as Log\_Hour  
FROM project\_activity\_log alog  
JOIN users u ON alog.created\_by \= u.id  
GROUP BY u.user\_email, year, month\_id;

**Log Description (১০%):** লগের ডেসক্রিপশনের মান Sentiment Analysis-এর মাধ্যমে যাচাই করা হয়।

\# Log Description Scoring Logic (Reference: perform\_crm\_df.py)  
def sentiment\_score\_discrete(text):  
    \# TextBlob library is used to calculate polarity  
    polarity \= TextBlob(str(text)).sentiment.polarity  
    if polarity \== 1: return 100  
    elif polarity \== 0: return 60  
    else: return 40

#### **Segment B: Discipline & Leadership Assessment (৫০ Marks)**

**Office Discipline (১০ Marks):** এটি উপস্থিতি এবং সময়ানুবর্তিতার ওপর ভিত্তি করে একটি অটোমেটেড স্কোর।

\-- Discipline Query (Reference: base\_attendence.sql)  
SELECT  
user\_email,  
LEAST(ROUND(((present \- late\_days) / actual\_work\_days) \* 100, 2), 100\) AS attendance\_score  
FROM (  
    SELECT  
    u.user\_email, ma.present, ma.work\_days,  
    (ma.work\_days \- ma.leave \- ma.day\_off) as actual\_work\_days,  
    FLOOR(ma.late\_attendance/3) as late\_days  
    FROM monthly\_attendance\_summary ma  
    JOIN users u ON ma.employee\_id \= u.employee\_id  
) tmp;

**Team Lead Assessment (৪০ Marks):**

* Critical Thinking & Problem Solving: ১০ Marks  
* Performance Agreement (KPI): ১৫ Marks  
* Team Lead General Assessment: ১৫ Marks

### **১.২ Final Adjustment & Reward Logic**

এক্সেপশনাল পারফরম্যান্স রিকগনাইজ করার জন্য সর্বোচ্চ ৫ Reward Marks যোগ করা হয়।

\-- Reward Score Query (Reference: reword\_scores.sql)  
SELECT user\_email, ROUND(((tran\_avg\_re\_scores \* 5\) / 140), 2\) as consolidate\_reword\_score  
FROM (  
    SELECT user\_email,  
    CASE WHEN AVG(attendance\_score \+ loghour\_score \+ score\_tl \+ score\_pc) \>= 140 THEN 140  
    ELSE AVG(attendance\_score \+ loghour\_score \+ score\_tl \+ score\_pc) END as tran\_avg\_re\_scores  
    FROM scoring\_summary GROUP BY user\_email  
) tmp;

**Final Normalization Formula:** Final Score \= ((Base Total \+ Reward Marks) / 105\) \* 100

### **১.৩ Evaluation Summary Table (Developer)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---: |
| Technical Activity | Quality Check \+ Logs | (Comp 1 \+ Comp 2\) / 2 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Problem Solving | TL Assessment | Manual Entry | ১০ |
| KPI Agreement | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | TL Assessment | Manual Entry | ১৫ |
| Reward Points | reword\_scores.sql | System Query | \+৫ |
| **Final Score** | **\-** | **Adjusted to 100 Scale** | **১০০** |

## 

## **২. For Developers (SQA) Performance Evaluation**

SQA ইঞ্জিনিয়ারদের কাজের মান এবং প্রফেশনালিজম মূল্যায়নের জন্যও একই standardized scoring methodology ব্যবহার করা হয়।

### **২.১ Score Calculation Structure**

**Work Logs (৯০%):** এটি SQA-দের লগ আওয়ারের পরিমাণ এবং প্রজেক্ট টাইপের ওপর ভিত্তি করে নির্ধারিত হয়।

\-- SQA Work Log Query  
SELECT u.employee\_id, u.user\_email, YEAR(alog.work\_dt) AS year, MONTH(alog.work\_dt) AS month\_id,  
team.team\_name AS Team, CONCAT(u.user\_first\_name, ' ', u.user\_last\_name) AS Employee,  
alog.description AS Description, ROUND(IFNULL(TIME\_TO\_SEC(CONCAT(alog.work\_duation,':00'))/3600,0),2) AS Log\_Hour  
FROM project\_activity\_log alog  
LEFT JOIN users u ON alog.created\_by=u.id  
LEFT JOIN team\_members tm ON u.id=tm.user\_id AND tm.status=1  
LEFT JOIN team\_info team ON tm.team\_id=team.id  
WHERE u.user\_status='active' AND u.employee\_id IN ('20230013', '20230007', '20220017', '20240019', '20210011', '20240026', '20240027', '20240038', '20230032')  
AND YEAR(alog.work\_dt) \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)  
AND MONTH(alog.work\_dt) \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH);

**Office Discipline (১০ Marks):**

\-- SQA Attendance Query  
SELECT user\_email, Team\_name, Employee, year\_at, month\_id\_at,  
LEAST(ROUND(((present-late\_days)/actual\_work\_days) \* 100, 2),100) AS attendance\_score  
FROM (  
    SELECT tm.team\_name AS Team\_name, ma.resource\_name AS Employee, u.user\_email,  
    YEAR(ma.month) AS year\_at, MONTH(ma.month) AS month\_id\_at,  
    ma.work\_days, (ma.work\_days \- ma.leave \- ma.day\_off) AS actual\_work\_days,  
    ma.present, FLOOR(ma.late\_attendance/3) AS late\_days  
    FROM monthly\_attendance\_summary ma  
    LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status='active'  
    LEFT JOIN team\_members tmm ON u.id=tmm.user\_id AND tmm.status=1  
    LEFT JOIN team\_info tm ON tmm.team\_id=tm.id AND tm.status=1  
    WHERE u.employee\_id IN ('20230013', '20230007', '20220017', '20240019', '20210011', '20240026', '20240027', '20240038', '20230032')  
) tmp  
WHERE tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)  
AND tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH);

**Team Lead Assessment (৪০ Marks):**

* Bug Identification & Critical Thinking: ১০ Marks  
* Performance Agreement (Test Coverage/KPI): ১৫ Marks  
* Team Lead General Assessment: ১৫ Marks

### **২.২ Evaluation Summary Table (SQA)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---: |
| Technical Activity | SQA Quality Check \+ Logs | (Comp 1 \+ Comp 2\) / 2 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Bug Identification | TL Assessment | Manual Entry | ১০ |
| KPI Agreement | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | TL Assessment | Manual Entry | ১৫ |

## **৩. For Application Team Performance Evaluation**

অ্যাপ্লিকেশন টিমের কাজের মূল্যায়ন এবং প্রফেশনালিজম যাচাই করার জন্য একটি নির্দিষ্ট scoring methodology ব্যবহার করা হয়।

### **৩.১ Score Calculation Structure**

**Work Logs (৯০%):** এটি সাপোর্ট বা অ্যাক্টিভিটি লগের পরিমাণের ওপর ভিত্তি করে নির্ধারিত হয়।

\-- Application Team Work Log Query  
SELECT u.employee\_id, u.user\_email, YEAR(alog.work\_dt) AS year, MONTH(alog.work\_dt) AS month\_id,  
team.team\_name AS Team, CONCAT(u.user\_first\_name, ' ', u.user\_last\_name) AS Employee,  
CASE WHEN alog.issue\_log\_details IS NULL OR alog.issue\_log\_details \= '' THEN alog.issue\_details ELSE alog.issue\_log\_details END AS Description,  
ROUND(IFNULL(TIME\_TO\_SEC(CONCAT(alog.work\_duation,':00'))/3600,0),2) AS Log\_Hour  
FROM project\_activity\_log alog  
LEFT JOIN users u ON alog.created\_by=u.id  
LEFT JOIN team\_members tm ON u.id=tm.user\_id AND tm.status=1  
LEFT JOIN team\_info team ON tm.team\_id=team.id  
WHERE u.user\_status='active' AND u.employee\_id IN ('20110002', '20190011', '20190003', '20230006', '20230023', '20220019')  
AND YEAR(alog.work\_dt) \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)  
AND MONTH(alog.work\_dt) \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH);

**Team Lead Assessment (৪০ Marks):**

* Support Readiness & Problem Solving: ১০ Marks  
* Performance Agreement (SLA/KPI): ১৫ Marks  
* Team Lead General Assessment: ১৫ Marks

### **৩.২ Evaluation Summary Table (Application Team)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---: |
| Technical Activity | Support Logs (EBS/Codelab) | Component 2 Scaled to 50 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Support Readiness | TL Assessment | Manual Entry | ১০ |
| KPI Agreement | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | TL Assessment | Manual Entry | ১৫ |

## **৪. For Business Team Performance Evaluation**

বিজনেস টিমের কাজের মূল্যায়ন এবং প্রফেশনালিজম যাচাই করার জন্য তাদের সেলস, মিটিং এবং লিড জেনারেশন ডাটার ওপর ভিত্তি করে একটি নির্দিষ্ট scoring methodology ব্যবহার করা হয়।

### **৪.১ Score Calculation Structure**

#### **Segment A: Functional Performance (Sales & CRM Activities) (৫০ Marks)**

* **Lead Generation (Lead Score \- ৪০%):** crm\_leads টেবিল থেকে প্রোডাক্ট লাইনের ওয়েটেজ অনুযায়ী।  
* **Conversion (Opportunity Score \- ৪০%):** কতগুলো লিড 'Converted' হলো তার ওপর ভিত্তি করে।  
* **Client Engagement (Meeting Score \- ২০%):** crm\_meeting টেবিল অনুযায়ী মোট কতগুলো মিটিং সম্পন্ন হয়েছে তার ওপর ভিত্তি করে।

\# Functional Score Logic for Business Team  
merged\_df\['monthly\_functional\_score'\] \= (  
    merged\_df\['leadScore'\] \* 0.4 \+  
    merged\_df\['oppScore'\] \* 0.4 \+  
    merged\_df\['meetingScore'\] \* 0.2  
)

#### **Segment B: Discipline & Leadership Assessment (৫০ Marks)**

**Office Discipline (১০ Marks):** উপস্থিতি এবং সময়ানুবর্তিতার ওপর ভিত্তি করে বিগত কয়েক মাসের অ্যাভারেজ।

\-- Business Team Attendance Query (Average)  
SELECT user\_email, Team\_name, Employee, '2026-01-01' AS date, ROUND(AVG(attendance\_score)) AS avg\_atten\_scores  
FROM (  
    SELECT u.user\_email, tm.team\_name AS Team\_name, ma.resource\_name AS Employee,  
    IFNULL(LEAST(ROUND(((present-late\_days)/actual\_work\_days) \* 100, 2),100),0) AS attendance\_score  
    FROM monthly\_attendance\_summary ma  
    LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status='active'  
    LEFT JOIN team\_members tmm ON u.id=tmm.user\_id AND tmm.status=1  
    LEFT JOIN team\_info tm ON tmm.team\_id=tm.id AND tm.status=1  
    WHERE u.employee\_id IN ('20190009','20230036','20200006','20220005','20230039','20240035')  
    AND tm.id=45 AND ma.month BETWEEN '2025-10-01' AND '2026-01-31'  
) tmp GROUP BY user\_email, Team\_name, Employee;

**Management Assessment (৪০ Marks):**

* Strategic Planning & Problem Solving: ১০ Marks  
* Sales Target & KPI Agreement: ১৫ Marks  
* Management General Assessment: ১৫ Marks

### **৪.২ Evaluation Summary Table (Business Team)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---: |
| Functional Activity | CRM (Leads \+ Meetings \+ Opps) | (Lead\*0.4 \+ Opp\*0.4 \+ Meet\*0.2) / 2 | ৫০ |
| Office Discipline | base\_attendence.sql (Average) | Automated Query | ১০ |
| Strategic Planning | Management Assessment | Manual Entry | ১০ |
| Sales Target / KPI | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | Management Assessment | Manual Entry | ১৫ |

## **৫. For Finance Team Performance Evaluation**

ফাইন্যান্স টিমের কাজের মূল্যায়ন তাদের টাস্ক ম্যানেজমেন্ট, ইস্যু রেজোলিউশন এবং ডিসিপ্লিন ডাটার ওপর ভিত্তি করে করা হয়।

### **৫.১ Score Calculation Structure**

* **Segment A: Task Management & Activity Logs (৫০ Marks):** ফাইন্যান্স টিমের ফাংশনাল পারফরম্যান্স মূলত Codelab বা ইস্যু ট্র্যাকিং সিস্টেমের ডাটার ওপর নির্ভর করে। এটি মেম্বারদের অ্যাসাইন করা টাস্ক (total\_assigned) এবং সমাধান করা টাস্কের (total\_resolved) অনুপাতের ওপর ভিত্তি করে নির্ধারিত হয়।

\-- Finance Team Task Resolution Query  
SELECT   
u.email AS user\_email,  
COUNT(i.id) AS total\_assigned,  
SUM(CASE WHEN i.closed\_at IS NOT NULL AND i.closed\_by\_id \= u.id THEN 1 ELSE 0 END) AS total\_resolved  
FROM issues i  
JOIN users u ON i.author\_id \= u.id  
WHERE u.state \= 'active'

* **Segment B: Discipline & Leadership Assessment (৫০ Marks):**  
  * Office Discipline (১০ Marks): উপস্থিতি এবং সময়ানুবর্তিতা।  
  * Financial Accuracy & Problem Solving (১০ Marks)।  
  * Task/KPI Agreement (১৫ Marks)।  
  * Management General Assessment (১৫ Marks)।  
* SELECT user\_email,Team\_name,Employee,\`year\_at\`,\`month\_id\_at\`,  
* IFNULL(LEAST(ROUND(((present\-late\_days)/actual\_work\_days) \* 100, 2),100),0) AS attendance\_score  
*      
* FROM  
* (SELECT tm.team\_name Team\_name, ma.resource\_name Employee, ma.employee\_id,ma.resource\_name,ma.team,u.user\_email,  
* YEAR(ma.month) \`year\_at\`,MONTH(ma.month) \`month\_id\_at\`,  
* ma.work\_days,  
* (ma.work\_days\-ma.leave\-ma.day\_off) actual\_work\_days,  
* ma.present,  
* ma.leave,ma.day\_off,  
* ma.absent,ma.late\_attendance,  
* FLOOR(ma.late\_attendance/3) late\_days  
* FROM monthly\_attendance\_summary ma  
* LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status\='active'  
* LEFT JOIN team\_members tmm ON u.id\=tmm.user\_id AND tmm.status\=1  
* LEFT JOIN team\_info tm ON tmm.team\_id\=tm.id AND tm.status\=1  
* WHERE u.user\_email IS NOT NULL  AND  
* u.employee\_id IN(  
*   
*        \-- Suply\_chain  
*   
* 20070001  
*   
*       )  
*   
* ) tmp  
* WHERE (tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH))  
*   AND (tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH));

### **৫.২ Evaluation Summary Table (Finance Team)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---- |
| Functional Activity | Task Resolution (Codelab Issues) | Resolved / Assigned scaled to 50 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Financial Accuracy | Management Assessment | Manual Entry | ১০ |
| Task / KPI | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | Management Assessment | Manual Entry | ১৫ |

## **৬. For GSD Team Performance Evaluation**

GSD (General Service Department) বা সাপোর্ট টিমের কাজের মূল্যায়ন মূলত তাদের সমাধান করা Support Tickets এবং টাস্কের ওপর ভিত্তি করে করা হয়।

### **৬.১ Score Calculation Structure**

#### **Segment A: Functional Performance (Support Tickets) (৫০ Marks)**

এই অংশটি GSD টিমের সাপোর্ট টিকিট হ্যান্ডলিংয়ের ওপর নির্ভর করে। support\_ticket\_info টেবিল থেকে নির্দিষ্ট স্ট্যাটাস অনুযায়ী টিকিটের সংখ্যা কাউন্ট করে স্কোর নির্ধারণ করা হয়।

* ৫০ বা তার বেশি টিকিট \= ১০০  
* ৪০ বা তার বেশি টিকিট \= ৮০  
* ৩০ বা তার বেশি টিকিট \= ৭০  
* ২০ বা তার বেশি টিকিট \= ৬০  
* এর কম হলে \= ৫০

\-- GSD Team Support Ticket Score Query (Reference: tickets.sql)  
SELECT \*,  
CASE WHEN tickets\>=50 THEN 100  
WHEN tickets\>=40 THEN 80   
WHEN tickets\>=30 THEN 70   
WHEN tickets\>=20 THEN 60  
ELSE 50  
END AS tickets\_score  
FROM  
(SELECT user\_email,Employee,year\_at,month\_id\_at,COUNT(id) tickets  
FROM  
(SELECT ti.id,u.user\_email,CONCAT\_WS('',u.user\_first\_name,u.user\_middle\_name,u.user\_last\_name) Employee,  
ti.support\_regrading,  
YEAR(ti.updated\_at) \`year\_at\`,MONTH(ti.updated\_at) \`month\_id\_at\`  
FROM \`support\_ticket\_info\` ti  
LEFT JOIN users u ON ti.assigned\_to=u.id AND u.user\_status='active'  
WHERE u.employee\_id IN(20210001,20150003,20230012,20220012,20240015) AND ti.status=23 ) tmp  
WHERE (tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH))  
AND (tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH))  
GROUP BY user\_email ) t;

#### **Segment B: Discipline & Leadership Assessment (৫০ Marks)**

**Office Discipline (১০ Marks):** উপস্থিতি এবং সময়ানুবর্তিতার ওপর ভিত্তি করে অটোমেটেড স্কোর।

\-- GSD Team Attendance Query (Reference: base\_attendence.sql)  
SELECT user\_email,Team\_name,Employee,\`year\_at\`,\`month\_id\_at\`,  
IFNULL(LEAST(ROUND(((present-late\_days)/actual\_work\_days) \* 100, 2),100),0) AS attendance\_score  
FROM  
(SELECT tm.team\_name Team\_name, ma.resource\_name Employee, ma.employee\_id,ma.resource\_name,ma.team,u.user\_email,  
YEAR(ma.month) \`year\_at\`,MONTH(ma.month) \`month\_id\_at\`,  
ma.work\_days,  
(ma.work\_days-ma.leave-ma.day\_off) actual\_work\_days,  
ma.present,  
ma.leave,ma.day\_off,  
ma.absent,ma.late\_attendance,  
FLOOR(ma.late\_attendance/3) late\_days  
FROM monthly\_attendance\_summary ma  
LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status='active'  
LEFT JOIN team\_members tmm ON u.id=tmm.user\_id AND tmm.status=1  
LEFT JOIN team\_info tm ON tmm.team\_id=tm.id AND tm.status=1  
WHERE u.user\_email IS NOT NULL  AND  
u.employee\_id IN(20210001,20150003,20230012,20220012,20240015) \-- GSD Team  
) tmp  
WHERE (tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)   
AND tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH));

* **Team Lead / Management Assessment (৪০ Marks):**  
  * Support Readiness & Issue Handling: ১০ Marks  
  * Task / KPI Agreement: ১৫ Marks  
  * Management General Assessment: ১৫ Marks

### **৬.২ Evaluation Summary Table (GSD Team)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---- |
| Functional Activity | Support Tickets Logic | Tickets Handled Scaled to 50 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Support Readiness | Management Assessment | Manual Entry | ১০ |
| Task / KPI | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | Management Assessment | Manual Entry | ১৫ |
| Reward Points | reword\_scores.sql | System Query | \+৫ |
| **Final Score** |  |  | **১০০** |

## **৭. For Haj Helpdesk Team Performance Evaluation**

Haj Helpdesk বা সাপোর্ট টিমের কাজের মূল্যায়ন মূলত তাদের CRM লগ আওয়ার্স এবং সমাধান করা Support Tickets এর ওপর ভিত্তি করে করা হয়।

### **৭.১ Score Calculation Structure**

#### **Segment A: Functional Performance (CRM Logs & Support Tickets) (৫০ Marks)**

এই সেগমেন্টটি দুটি ভাগে বিভক্ত এবং এই দুটির সমন্বয়ে (Weighted Average) ফাংশনাল স্কোর তৈরি হয়:

* **CRM Log Score (৮০% ওয়েটেজ):** perform\_crm.sql এবং perform\_crm\_df.py থেকে প্রাপ্ত লগ আওয়ার্স এবং সেন্টিমেন্ট স্কোরের ওপর ভিত্তি করে।  
* **Tickets Evaluation Score (২০% ওয়েটেজ):** tickets\_score.sql এবং functional\_task\_tickets.py থেকে প্রাপ্ত। এটি আবার দুটি মেট্রিক্স দিয়ে গঠিত:  
  * **Total Tickets Solved (৭০%):** ৩০+ টিকিট হলে ১০০, ২০+ হলে ৮০, ১০+ হলে ৭০, \>০ হলে ৬০।  
  * **Average Taken Days (৩০%):** ২ দিন বা তার কম হলে ১০০, বেশি হলে ৬০।

\# Haj Helpdesk Functional Score Logic (Reference: functional\_task\_tickets.py)  
tickets\_df\['tickets\_evaluation\_score'\] \= (  
    tickets\_df\['monthly\_tickets\_score'\] \* 0.7 \+  
    tickets\_df\['monthly\_ticket\_resolved\_score'\] \* 0.3  
)

\# Final Functional Score merging CRM and Tickets  
df\['monthly\_functional\_score'\] \= (df\['final\_crm\_log\_score'\] \* 0.8) \+ (df\['tickets\_evaluation\_score'\] \* 0.2)

#### **Segment B: Discipline & Leadership Assessment (৫০ Marks)**

**Office Discipline (১০ Marks):** উপস্থিতি এবং সময়ানুবর্তিতা (base\_attendence.sql)।

* **Team Lead Assessment (৪০ Marks):**  
  * Support Readiness & Issue Handling: ১০ Marks  
  * KPI Agreement: ১৫ Marks  
  * Leadership General Assessment: ১৫ Marks

###  **Required Queries**

**CRM Logs Query (\`perform\_crm.sql\`):**

SELECT   
    u.employee\_id, u.user\_email, YEAR(alog.work\_dt) \`year\`, MONTH(alog.work\_dt) \`month\_id\`,  
    team.\`team\_name\` AS Team,  
    CONCAT(u.\`user\_first\_name\`, ' ', u.\`user\_middle\_name\`, ' ', u.\`user\_last\_name\`) AS Employee,     
    p.ref\_no AS \`Project Code\`,   
    alog.\`description\` AS 'Description',  
    ROUND(IFNULL(TIME\_TO\_SEC(CONCAT(alog.\`work\_duation\`,':00'))/60/60,0),2) Log\_Hour  
FROM \`project\_activity\_log\` alog   
LEFT JOIN users u ON alog.\`created\_by\`=u.id   
LEFT JOIN (SELECT \* FROM \`team\_members\` WHERE \`status\`=1) tm ON u.id=tm.\`user\_id\`   
LEFT JOIN \`team\_info\` team ON tm.\`team\_id\`=team.\`id\`  
LEFT JOIN \`project\_info\` p ON alog.\`project\_id\`=p.\`id\`   
LEFT JOIN \`project\_types\` pt ON p.\`type\`=pt.name   
WHERE u.user\_status='active'   
  AND u.employee\_id IN (20160005, 20220016, 20220008, 20090001, 20090002, 20110001\)

**Tickets Score Query (\`tickets\_score.sql\`):**

SELECT user\_email, assigned\_parson,  
    CASE  
        WHEN total\_tickets \>= 30 THEN 100  
        WHEN total\_tickets \>= 20 THEN 80  
        WHEN total\_tickets \>= 10 THEN 70  
        WHEN total\_tickets \> 0  THEN 60  
        WHEN total\_tickets \= 0  THEN 40  
        ELSE 50  
    END AS monthly\_tickets\_score,  
    CASE  
        WHEN average\_taken\_days \<= 2 THEN 100  
        ELSE 60  
    END AS monthly\_ticket\_resolved\_score  
FROM (  
    SELECT user\_email, assigned\_parson, SUM(tickets) total\_tickets, ROUND(AVG(avg\_taken\_days),2) average\_taken\_days  
    FROM (  
        SELECT u.user\_email, CONCAT\_WS(' ',u.user\_first\_name,u.user\_middle\_name,u.user\_last\_name) assigned\_parson,  
               COUNT(plh.tracking\_no) tickets, AVG(IFNULL(DATEDIFF(plh.updated\_at,plh.created\_at),0)) avg\_taken\_days  
        FROM process\_list\_hist plh  
        LEFT JOIN users u ON plh.updated\_by=u.id AND u.user\_status='active' AND u.access\_role='Employee'  
        WHERE plh.updated\_at BETWEEN '2026-01-01' AND '2026-01-31'   
          AND u.employee\_id IN (20160005, 20220016, 20220008, 20090001, 20090002, 20110001\)  
        GROUP BY u.user\_email, assigned\_parson  
    ) tm GROUP BY user\_email, assigned\_parson  
) final\_t;

**Office Discipline Query (\`base\_attendence.sql\`):**

SELECT user\_email, Team\_name, Employee, \`year\_at\`, \`month\_id\_at\`,  
       IFNULL(LEAST(ROUND(((present-late\_days)/actual\_work\_days) \* 100, 2),100),0) AS attendance\_score     
FROM (  
    SELECT tm.team\_name Team\_name, ma.resource\_name Employee, ma.employee\_id, u.user\_email,  
           YEAR(ma.month) \`year\_at\`, MONTH(ma.month) \`month\_id\_at\`,  
           ma.work\_days, (ma.work\_days-ma.leave-ma.day\_off) actual\_work\_days,  
           ma.present, FLOOR(ma.late\_attendance/3) late\_days  
    FROM monthly\_attendance\_summary ma  
    LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status='active'  
    LEFT JOIN team\_members tmm ON u.id=tmm.user\_id AND tmm.status=1  
    LEFT JOIN team\_info tm ON tmm.team\_id=tm.id AND tm.status=1  
    WHERE u.user\_email IS NOT NULL    
      AND u.employee\_id IN (20160005, 20220016, 20220008, 20090001, 20090002, 20110001\)  
) tmp  
WHERE tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)  
  AND tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH);

### **৭.২ Evaluation Summary Table (Haj Helpdesk Team)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---- |
| Functional Activity | CRM Logs \+ Tickets Evaluation | (CRM Score \* 0.8) \+ (Tickets Score \* 0.2) scaled to 50 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Support Readiness | TL Assessment | Manual Entry | ১০ |
| KPI Agreement | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | TL Assessment | Manual Entry | ১৫ |
| **Final Score** |  |  | **১০০** |

## **৮. For Support Teams (Impl\&ITS, Onsite Support, Production, Tech Support)**

ইমপ্লিমেন্টেশন, আইটিএস, অনসাইট সাপোর্ট, প্রোডাকশন এবং টেক সাপোর্ট টিমের কাজের মূল্যায়ন Haj Helpdesk-এর মতই CRM লগ এবং সাপোর্ট টিকিটের ওপর ভিত্তি করে করা হয়।

### **৮.১ Score Calculation Structure**

#### **Segment A: Functional Performance (CRM Logs & Support Tickets) (৫০ Marks)**

এই সেগমেন্টটি দুটি ভাগে বিভক্ত এবং এই দুটির সমন্বয়ে (Weighted Average) ফাংশনাল স্কোর তৈরি হয়:

* **CRM Log Score (৮০% ওয়েটেজ):** funcational\_log\_activities.py এবং perform\_crm.sql থেকে প্রাপ্ত লগ আওয়ার্স এবং সেন্টিমেন্ট স্কোরের ওপর ভিত্তি করে। লগ আওয়ারকে একটি কাস্টম ট্রান্সফরমেশন ফাংশন (যেমন: \>১৬০ হলে ১০০, \>১৪০ হলে ৮০ ইত্যাদি) ব্যবহার করে নরমালাইজ করা হয়।  
* **Tickets Evaluation Score (২০% ওয়েটেজ):** functional\_task\_tickets.py এবং tickets\_score.sql থেকে প্রাপ্ত। এটি আবার দুটি মেট্রিক্স দিয়ে গঠিত:  
  * **Total Tickets Solved (৭০%):** ৩০+ টিকিট হলে ১০০, ২০+ হলে ৮০, ১০+ হলে ৭০, \>০ হলে ৬০।  
  * **Average Taken Days (৩০%):** ২ দিন বা তার কম হলে ১০০, বেশি হলে ৬০।

\# Support Teams Functional Score Logic (Reference: functional\_marged\_scores.py)  
merged\_functional\_df\['monthly\_functional\_score'\] \= (  
    merged\_functional\_df\['tickets\_evaluation\_score'\] \* 0.2 \+  
    merged\_functional\_df\['final\_crm\_log\_score'\] \* 0.8  
)

#### **Segment B: Discipline & Leadership Assessment (৫০ Marks)**

**Office Discipline (১০ Marks):** উপস্থিতি এবং সময়ানুবর্তিতা (base\_attendence.sql)।

* **Team Lead Assessment (৪০ Marks):**  
  * Support Readiness & Issue Handling: ১০ Marks  
  * KPI Agreement: ১৫ Marks  
  * Leadership General Assessment: ১৫ Marks

###  **Required Queries**

**CRM Logs Query (\`perform\_crm.sql\`):**

SELECT   
    u.employee\_id, u.user\_email, YEAR(alog.work\_dt) \`year\`, MONTH(alog.work\_dt) \`month\_id\`,  
    team.\`team\_name\` AS Team,  
    CONCAT(u.\`user\_first\_name\`, ' ', u.\`user\_middle\_name\`, ' ', u.\`user\_last\_name\`) AS Employee,     
    alog.\`description\` AS 'Description',  
    ROUND(IFNULL(TIME\_TO\_SEC(CONCAT(alog.\`work\_duation\`,':00'))/60/60,0),2) Log\_Hour  
FROM \`project\_activity\_log\` alog   
LEFT JOIN users u ON alog.\`created\_by\`=u.id   
LEFT JOIN (SELECT \* FROM \`team\_members\` WHERE \`status\`=1) tm ON u.id=tm.\`user\_id\`   
LEFT JOIN \`team\_info\` team ON tm.\`team\_id\`=team.\`id\`  
WHERE u.user\_status='active'   
  AND u.employee\_id IN (  
      \-- Imp & ITs, Onsite Support, Production, Tech Support  
      20240006, 20050002, 20240039, 20220020, 20160009, 20230026, 20240042,  
      20240018, 20220018, 20240023, 20240034, 20240037, 20220002, 20240025,  
      20240028, 20240010, 20230008, 20150002, 20150004, 20230003, 20240021, 20240020  
  )

**Tickets Score Query (\`tickets\_score.sql\`):**

SELECT user\_email, assigned\_parson,  
    CASE  
        WHEN total\_tickets \>= 30 THEN 100  
        WHEN total\_tickets \>= 20 THEN 80  
        WHEN total\_tickets \>= 10 THEN 70  
        WHEN total\_tickets \> 0  THEN 60  
        WHEN total\_tickets \= 0  THEN 40  
        ELSE 50  
    END AS monthly\_tickets\_score,  
    CASE  
        WHEN average\_taken\_days \<= 2 THEN 100  
        ELSE 60  
    END AS monthly\_ticket\_resolved\_score  
FROM (  
    SELECT user\_email, assigned\_parson, SUM(tickets) total\_tickets, ROUND(AVG(avg\_taken\_days),2) average\_taken\_days  
    FROM (  
        SELECT u.user\_email, CONCAT\_WS(' ',u.user\_first\_name,u.user\_middle\_name,u.user\_last\_name) assigned\_parson,  
               COUNT(plh.tracking\_no) tickets, AVG(IFNULL(DATEDIFF(plh.updated\_at,plh.created\_at),0)) avg\_taken\_days  
        FROM process\_list\_hist plh  
        LEFT JOIN users u ON plh.updated\_by=u.id AND u.user\_status='active'  
        WHERE plh.updated\_at BETWEEN '2026-01-01' AND '2026-01-31'   
          AND u.employee\_id IN (  
              20240006, 20050002, 20240039, 20220020, 20160009, 20230026, 20240042,  
              20240018, 20220018, 20240023, 20240034, 20240037, 20220002, 20240025,  
              20240028, 20240010, 20230008, 20150002, 20150004, 20230003, 20240021, 20240020  
          )  
        GROUP BY u.user\_email, assigned\_parson  
    ) tm GROUP BY user\_email, assigned\_parson  
) final\_t;

**Office Discipline Query (\`base\_attendence.sql\`):**

SELECT user\_email, Team\_name, Employee, \`year\_at\`, \`month\_id\_at\`,  
       IFNULL(LEAST(ROUND(((present-late\_days)/actual\_work\_days) \* 100, 2),100),0) AS attendance\_score     
FROM (  
    SELECT tm.team\_name Team\_name, ma.resource\_name Employee, ma.employee\_id, u.user\_email,  
           YEAR(ma.month) \`year\_at\`, MONTH(ma.month) \`month\_id\_at\`,  
           ma.work\_days, (ma.work\_days-ma.leave-ma.day\_off) actual\_work\_days,  
           ma.present, FLOOR(ma.late\_attendance/3) late\_days  
    FROM monthly\_attendance\_summary ma  
    LEFT JOIN users u ON ma.employee\_id \= u.employee\_id AND u.user\_status='active'  
    LEFT JOIN team\_members tmm ON u.id=tmm.user\_id AND tmm.status=1  
    LEFT JOIN team\_info tm ON tmm.team\_id=tm.id AND tm.status=1  
    WHERE u.user\_email IS NOT NULL    
      AND u.employee\_id IN (  
          20240006, 20050002, 20240039, 20220020, 20160009, 20230026, 20240042,  
          20240018, 20220018, 20240023, 20240034, 20240037, 20220002, 20240025,  
          20240028, 20240010, 20230008, 20150002, 20150004, 20230003, 20240021, 20240020  
      )  
) tmp  
WHERE tmp.year\_at \= YEAR(CURRENT\_DATE \- INTERVAL 1 MONTH)  
  AND tmp.month\_id\_at \= MONTH(CURRENT\_DATE \- INTERVAL 1 MONTH);

### **৮.২ Evaluation Summary Table (Support Teams)**

| Category | Source File/Method | Weighting Method | Max Marks |
| :---- | :---- | :---- | :---- |
| Functional Activity | CRM Logs \+ Tickets Evaluation | (CRM Score \* 0.8) \+ (Tickets Score \* 0.2) scaled to 50 | ৫০ |
| Office Discipline | base\_attendence.sql | Automated Query | ১০ |
| Support Readiness | TL Assessment | Manual Entry | ১০ |
| KPI Agreement | Performance Agreement | Manual Entry | ১৫ |
| Leadership Review | TL Assessment | Manual Entry | ১৫ |
| **Final Score** |  |  | **১০০** |

