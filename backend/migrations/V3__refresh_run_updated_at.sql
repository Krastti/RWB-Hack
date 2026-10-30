-- Keep V1/V2 checksums unchanged for installations that already applied them.
-- Every writer, including independent SQL workers, receives the same timestamp rule.
CREATE FUNCTION refresh_run_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER runs_refresh_updated_at
BEFORE UPDATE ON runs
FOR EACH ROW EXECUTE FUNCTION refresh_run_updated_at();
