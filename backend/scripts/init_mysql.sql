-- PTDRA MySQL 元数据建表脚本（PRD 3.2.3）
-- 亦可由 SQLAlchemy Base.metadata.create_all 自动创建；本脚本用于手工初始化。
CREATE DATABASE IF NOT EXISTS `rag_meta` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `rag_meta`;

CREATE TABLE IF NOT EXISTS documents (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_path     VARCHAR(512) NOT NULL UNIQUE,
    source_type   ENUM('tech_doc', 'personal_note') NOT NULL,
    title         VARCHAR(255),
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
