-- PTDRA MySQL 元数据建表脚本（PRD 3.2.3）
-- 亦可由 SQLAlchemy Base.metadata.create_all 自动创建；本脚本用于手工初始化。
CREATE DATABASE IF NOT EXISTS `rag_meta` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `rag_meta`;

CREATE TABLE IF NOT EXISTS documents (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_path     VARCHAR(512) NOT NULL UNIQUE,
    source_type   ENUM('tech_doc', 'personal_note', 'uploaded_doc') NOT NULL,
    title         VARCHAR(255),
    source_url    VARCHAR(512),
    content_hash  CHAR(64) NOT NULL,
    chunk_count   INT DEFAULT 0,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_ingested DATETIME,
    INDEX idx_source_type (source_type),
    INDEX idx_updated_at (updated_at),
    INDEX idx_content_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chunks (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    document_id   BIGINT NOT NULL,
    chunk_index   INT NOT NULL,
    chroma_id     VARCHAR(255) NOT NULL,
    heading_path  VARCHAR(512),
    token_count   INT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE KEY uk_chroma_id (chroma_id),
    INDEX idx_document (document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tags (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(64) NOT NULL UNIQUE,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS document_tags (
    document_id   BIGINT NOT NULL,
    tag_id        BIGINT NOT NULL,
    PRIMARY KEY (document_id, tag_id),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ingest_tasks (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id       VARCHAR(64) NOT NULL UNIQUE,
    status        ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
    file_count    INT DEFAULT 0,
    chunk_count   INT DEFAULT 0,
    error_message TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at  DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 知识库上传（TDA-26-10-2 / 7.1.3）
-- 旧库若 documents.source_type 仍为两值枚举，应用启动时会幂等 ALTER：
-- ALTER TABLE documents MODIFY COLUMN source_type
--   ENUM('tech_doc', 'personal_note', 'uploaded_doc') NOT NULL;
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS upload_tasks (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64) NOT NULL UNIQUE,
    status          VARCHAR(32) NOT NULL DEFAULT 'queued',
    engine          VARCHAR(32) NOT NULL DEFAULT 'auto',
    total_files     INT NOT NULL DEFAULT 0,
    processed_files INT NOT NULL DEFAULT 0,
    failed_files    INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at    DATETIME,
    INDEX idx_upload_task_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS upload_files (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    upload_id         VARCHAR(64) NOT NULL UNIQUE,
    task_id           VARCHAR(64),
    original_filename VARCHAR(512) NOT NULL,
    stored_path       VARCHAR(1024) NOT NULL,
    markdown_path     VARCHAR(1024),
    doc_format        VARCHAR(16) NOT NULL,
    file_size         BIGINT NOT NULL,
    content_hash      CHAR(64) NOT NULL,
    status            VARCHAR(32) NOT NULL DEFAULT 'queued',
    auto_index        TINYINT(1) NOT NULL DEFAULT 1,
    parse_engine      VARCHAR(32),
    chunk_size        INT NOT NULL DEFAULT 512,
    chunk_overlap     INT NOT NULL DEFAULT 64,
    progress          INT NOT NULL DEFAULT 0,
    error_message     TEXT,
    document_id       BIGINT,
    chunk_count       INT NOT NULL DEFAULT 0,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_upload_file_status (status),
    INDEX idx_upload_file_task (task_id),
    INDEX idx_upload_file_hash (content_hash),
    CONSTRAINT fk_upload_file_document FOREIGN KEY (document_id)
        REFERENCES documents(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS upload_assets (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    upload_id   VARCHAR(64) NOT NULL,
    asset_type  VARCHAR(16) NOT NULL,
    name        VARCHAR(512) NOT NULL,
    rel_path    VARCHAR(1024) NOT NULL,
    page_no     INT,
    caption     TEXT,
    ocr_text    MEDIUMTEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_upload_asset_upload (upload_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 知识源注册表（语言学习资料爬取入库实施方案 步骤 1）
-- authority: A=官方文档 / B=优质社区教程
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_id  VARCHAR(64)  NOT NULL UNIQUE,
    name       VARCHAR(128) NOT NULL,
    domains    VARCHAR(512) NOT NULL,
    authority  ENUM('A', 'B') NOT NULL DEFAULT 'B',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
