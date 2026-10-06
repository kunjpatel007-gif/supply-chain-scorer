-- Run once against an existing database created from an older schema_setup.sql
BEGIN
    EXECUTE IMMEDIATE 'ALTER TABLE SELLER ADD (SellerName VARCHAR2(200))';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

BEGIN
    EXECUTE IMMEDIATE 'ALTER TABLE QUALITY_INSPECTION ADD (VaderSeverityWeight NUMBER(5,2))';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -1430 THEN RAISE; END IF;
END;
/

UPDATE SELLER
SET SellerName = SellerCity || ', ' || SellerState
WHERE SellerName IS NULL
  AND SellerCity IS NOT NULL
  AND SellerState IS NOT NULL;

COMMIT;
