-- WP05 PostgreSQL catalog schema. Applied only to a fresh WorldFuzz database.
-- Keep payload/metadata as TEXT: JSONB normalization would change legacy bytes.
BEGIN;
CREATE SCHEMA worldfuzz;
CREATE TABLE worldfuzz.corpus_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE worldfuzz.datasets (
    id TEXT PRIMARY KEY,
    metadata TEXT NOT NULL
);
CREATE TABLE worldfuzz.corpus_group_splits (
    group_id TEXT PRIMARY KEY,
    split TEXT NOT NULL CHECK (split IN ('train','validation','test')),
    rule TEXT NOT NULL,
    UNIQUE (group_id, split)
);
CREATE TABLE worldfuzz.episodes (
    id TEXT PRIMARY KEY,
    dataset TEXT NOT NULL REFERENCES worldfuzz.datasets(id),
    source_key TEXT NOT NULL,
    domain TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    label TEXT NOT NULL,
    group_id TEXT NOT NULL,
    split TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    UNIQUE (dataset, source_key),
    FOREIGN KEY (group_id, split) REFERENCES worldfuzz.corpus_group_splits(group_id, split)
);
CREATE INDEX episode_filters ON worldfuzz.episodes(domain,status,label,split);

CREATE FUNCTION worldfuzz.capture_group() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO worldfuzz.corpus_group_splits(group_id, split, rule)
        VALUES (NEW.group_id, NEW.split, 'sha256-v1') ON CONFLICT (group_id) DO NOTHING;
    IF NOT EXISTS (SELECT 1 FROM worldfuzz.corpus_group_splits
                   WHERE group_id=NEW.group_id AND split=NEW.split) THEN
        RAISE EXCEPTION 'group split conflict';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER capture_group BEFORE INSERT ON worldfuzz.episodes
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.capture_group();

CREATE FUNCTION worldfuzz.reject_group_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'group manifest is immutable';
END;
$$;
CREATE TRIGGER immutable_group BEFORE UPDATE OR DELETE ON worldfuzz.corpus_group_splits
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_group_change();

CREATE FUNCTION worldfuzz.reject_episode_group_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.group_id IS DISTINCT FROM OLD.group_id OR NEW.split IS DISTINCT FROM OLD.split THEN
        RAISE EXCEPTION 'episode group and split are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_episode_group BEFORE UPDATE ON worldfuzz.episodes
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_episode_group_change();

INSERT INTO worldfuzz.corpus_schema_migrations(version, name, applied_at)
    VALUES (1, '001_catalog', EXTRACT(EPOCH FROM clock_timestamp()));
COMMIT;
