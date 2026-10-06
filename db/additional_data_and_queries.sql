-- 15 SQL Commands

-- 1. INSERT into SELLER
INSERT INTO SELLER (SellerID, SellerName, SellerZipCodePrefix, SellerCity, SellerState) 
VALUES ('S999', 'New City, SP', '12345', 'New City', 'SP');

-- 2. INSERT into PRODUCT
INSERT INTO PRODUCT (ProductID, ProductCategoryName) 
VALUES ('P999', 'Electronics');

-- 3. INSERT into PURCHASE_ORDER
INSERT INTO PURCHASE_ORDER (PO_ID, OrderDate, ExpectedDeliveryDate, OrderStatus) 
VALUES ('PO999', SYSDATE - 10, SYSDATE + 5, 'delivered');

-- 4. INSERT into PO_LINE
INSERT INTO PO_LINE (PO_ID, LineNo, ProductID, SellerID, UnitPriceAtOrder, FreightValue, ShippingLimitDate) 
VALUES ('PO999', 1, 'P999', 'S999', 150.00, 20.00, SYSDATE - 8);

-- 5. INSERT into DELIVERY
INSERT INTO DELIVERY (DeliveryID, PO_ID, DeliveredCarrierDate, ActualDeliveryDate, SellerDispatchDelayDays, CarrierTransitDelayDays, DelayDays) 
VALUES (DELIVERY_SEQ.NEXTVAL, 'PO999', SYSDATE - 7, SYSDATE - 2, 1.5, 5.0, 0);

-- 6. INSERT into QUALITY_INSPECTION
INSERT INTO QUALITY_INSPECTION (InspectionID, PO_ID, InspectionDate, ReviewScore, RejectionFlag, ReviewAnswerTimestamp, ReviewCommentTitle, ReviewCommentMessage, DefectTypeID) 
VALUES (INSPECTION_SEQ.NEXTVAL, 'PO999', SYSDATE - 1, 5, 'Positive', SYSDATE, 'Great!', 'Arrived on time and in perfect condition.', NULL);

-- 7. UPDATE DELIVERY
UPDATE DELIVERY 
SET DelayDays = 2 
WHERE PO_ID = 'PO999';

-- 8. UPDATE SELLER
UPDATE SELLER 
SET SellerCity = 'Sao Paulo' 
WHERE SellerID = 'S999';

-- 9. DELETE FROM SELLER (using a dummy record to delete)
INSERT INTO SELLER (SellerID, SellerName, SellerZipCodePrefix, SellerCity, SellerState) VALUES ('S_DEL', 'Delete City, XX', '00000', 'Delete City', 'XX');
DELETE FROM SELLER WHERE SellerID = 'S_DEL';

-- 10. SELECT with WHERE
SELECT * FROM SELLER WHERE SellerCity = 'Sao Paulo';

-- 11. SELECT with GROUP BY and AGGREGATION
SELECT SellerState, COUNT(*) as TotalSellers 
FROM SELLER 
GROUP BY SellerState;

-- 12. SELECT with JOIN (3 tables)
SELECT po.PO_ID, p.ProductID, p.ProductCategoryName 
FROM PO_LINE pl 
JOIN PRODUCT p ON pl.ProductID = p.ProductID 
JOIN PURCHASE_ORDER po ON pl.PO_ID = po.PO_ID;

-- 13. SELECT with JOIN, GROUP BY, and AGGREGATION
SELECT s.SellerID, AVG(d.DelayDays) as AvgDelay 
FROM SELLER s 
JOIN PO_LINE pl ON s.SellerID = pl.SellerID 
JOIN DELIVERY d ON pl.PO_ID = d.PO_ID 
GROUP BY s.SellerID;

-- 14. SELECT with ORDER BY
SELECT * FROM QUALITY_INSPECTION 
WHERE ReviewScore <= 3 
ORDER BY InspectionDate DESC;

-- 15. SELECT with LEFT JOIN
SELECT s.SellerID, s.SellerCity, rs.WeightedRiskScore 
FROM SELLER s 
LEFT JOIN RISK_SCORE rs ON s.SellerID = rs.SellerID;


-- 5 PL/SQL Commands

-- 1. Anonymous Block
SET SERVEROUTPUT ON;
DECLARE
    v_max_delay NUMBER;
BEGIN
    SELECT MAX(DelayDays) INTO v_max_delay FROM DELIVERY;
    DBMS_OUTPUT.PUT_LINE('Maximum Delivery Delay is: ' || NVL(v_max_delay, 0) || ' days.');
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        DBMS_OUTPUT.PUT_LINE('No delivery data found.');
END;
/

-- 2. Function
CREATE OR REPLACE FUNCTION get_product_category (p_product_id IN VARCHAR2) 
RETURN VARCHAR2 
IS
    v_category VARCHAR2(100);
BEGIN
    SELECT ProductCategoryName INTO v_category 
    FROM PRODUCT 
    WHERE ProductID = p_product_id;
    
    RETURN v_category;
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        RETURN 'Unknown Category';
END;
/

-- 3. Procedure
CREATE OR REPLACE PROCEDURE add_new_seller (
    p_seller_id IN VARCHAR2,
    p_zip_code IN VARCHAR2,
    p_city IN VARCHAR2,
    p_state IN VARCHAR2
) 
IS
BEGIN
    INSERT INTO SELLER (SellerID, SellerZipCodePrefix, SellerCity, SellerState)
    VALUES (p_seller_id, p_zip_code, p_city, p_state);
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Seller ' || p_seller_id || ' added successfully.');
EXCEPTION
    WHEN DUP_VAL_ON_INDEX THEN
        DBMS_OUTPUT.PUT_LINE('Seller ' || p_seller_id || ' already exists.');
END;
/

-- 4. Trigger
CREATE OR REPLACE TRIGGER trg_po_order_date
BEFORE INSERT ON PURCHASE_ORDER
FOR EACH ROW
BEGIN
    IF :NEW.OrderDate IS NULL THEN
        :NEW.OrderDate := SYSDATE;
    END IF;
END;
/

-- 5. Package (Specification and Body)
CREATE OR REPLACE PACKAGE risk_mgmt_pkg AS
    PROCEDURE evaluate_seller_risk(p_seller_id VARCHAR2, p_eval_period VARCHAR2);
    FUNCTION get_seller_risk_score(p_seller_id VARCHAR2, p_eval_period VARCHAR2) RETURN NUMBER;
END risk_mgmt_pkg;
/

CREATE OR REPLACE PACKAGE BODY risk_mgmt_pkg AS
    PROCEDURE evaluate_seller_risk(p_seller_id VARCHAR2, p_eval_period VARCHAR2) IS
        v_dummy NUMBER;
    BEGIN
        -- Simplified logic for demonstration
        SELECT COUNT(*) INTO v_dummy FROM SELLER WHERE SellerID = p_seller_id;
        IF v_dummy > 0 THEN
            DBMS_OUTPUT.PUT_LINE('Evaluated risk for seller ' || p_seller_id || ' for period ' || p_eval_period);
        END IF;
    END evaluate_seller_risk;

    FUNCTION get_seller_risk_score(p_seller_id VARCHAR2, p_eval_period VARCHAR2) RETURN NUMBER IS
        v_score NUMBER(5,2);
    BEGIN
        SELECT WeightedRiskScore INTO v_score 
        FROM RISK_SCORE 
        WHERE SellerID = p_seller_id AND EvaluationPeriod = p_eval_period;
        RETURN v_score;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RETURN -1; -- indicates no score found
    END get_seller_risk_score;
END risk_mgmt_pkg;
/

COMMIT;
