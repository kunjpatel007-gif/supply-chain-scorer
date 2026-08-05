PROMPT 1. Row counts per table
--------------------------------------------------
SELECT 'SELLER' AS table_name, COUNT(*) AS row_count FROM SELLER
UNION ALL
SELECT 'PRODUCT', COUNT(*) FROM PRODUCT
UNION ALL
SELECT 'PURCHASE_ORDER', COUNT(*) FROM PURCHASE_ORDER
UNION ALL
SELECT 'DEFECT_TYPE', COUNT(*) FROM DEFECT_TYPE
UNION ALL
SELECT 'RISK_WEIGHT_CONFIG', COUNT(*) FROM RISK_WEIGHT_CONFIG
UNION ALL
SELECT 'PO_LINE', COUNT(*) FROM PO_LINE
UNION ALL
SELECT 'DELIVERY', COUNT(*) FROM DELIVERY
UNION ALL
SELECT 'QUALITY_INSPECTION', COUNT(*) FROM QUALITY_INSPECTION
UNION ALL
SELECT 'RISK_SCORE', COUNT(*) FROM RISK_SCORE
UNION ALL
SELECT 'ALERT_LOG', COUNT(*) FROM ALERT_LOG
UNION ALL
SELECT 'CORRECTIVE_ACTION', COUNT(*) FROM CORRECTIVE_ACTION
UNION ALL
SELECT 'PRICE_HISTORY', COUNT(*) FROM PRICE_HISTORY;

PROMPT 2. Top 10 highest-risk sellers
--------------------------------------------------
SELECT SellerID, WeightedRiskScore, RiskCategory, RiskTrend 
FROM RISK_SCORE 
ORDER BY WeightedRiskScore DESC 
FETCH FIRST 10 ROWS ONLY;

PROMPT 3. Sellers with most Late Delivery defects
--------------------------------------------------
SELECT pl.SellerID, COUNT(*) AS DefectCount
FROM QUALITY_INSPECTION qi
JOIN DEFECT_TYPE dt ON qi.DefectTypeID = dt.DefectTypeID
JOIN PO_LINE pl ON qi.PO_ID = pl.PO_ID
WHERE dt.DefectCategory = 'Late Delivery'
GROUP BY pl.SellerID 
ORDER BY COUNT(*) DESC 
FETCH FIRST 10 ROWS ONLY;

PROMPT 4. Alert log vs corrective action turnaround
--------------------------------------------------
SELECT 
    al.AlertID,
    al.AlertDate,
    ca.ActualResolutionDate,
    EXTRACT(DAY FROM (ca.ActualResolutionDate - al.AlertDate)) AS turnaround_days
FROM ALERT_LOG al
JOIN CORRECTIVE_ACTION ca ON al.AlertID = ca.AlertID
WHERE ca.ActualResolutionDate IS NOT NULL;

PROMPT 5. Data quality checks
--------------------------------------------------
-- Orders without deliveries
SELECT po.PO_ID 
FROM PURCHASE_ORDER po 
LEFT JOIN DELIVERY d ON po.PO_ID = d.PO_ID 
WHERE d.DeliveryID IS NULL;

-- Reviews without text
SELECT InspectionID 
FROM QUALITY_INSPECTION 
WHERE ReviewCommentMessage IS NULL;

-- Null checks example
SELECT PO_ID FROM PO_LINE WHERE UnitPriceAtOrder IS NULL;

PROMPT 6. Risk category distribution
--------------------------------------------------
SELECT RiskCategory, COUNT(*) AS CategoryCount
FROM RISK_SCORE 
GROUP BY RiskCategory;

PROMPT 7. Average review score by seller (top 10 worst)
--------------------------------------------------
SELECT pl.SellerID, AVG(qi.ReviewScore) AS AvgScore
FROM QUALITY_INSPECTION qi
JOIN PO_LINE pl ON qi.PO_ID = pl.PO_ID
GROUP BY pl.SellerID
ORDER BY AvgScore ASC
FETCH FIRST 10 ROWS ONLY;
