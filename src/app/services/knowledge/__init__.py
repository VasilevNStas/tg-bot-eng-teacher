"""Curated shared knowledge base (ADR-059)."""

from app.services.knowledge.model import KnowledgeSnippet, render_knowledge_block
from app.services.knowledge.service import KnowledgeService

__all__ = ["KnowledgeService", "KnowledgeSnippet", "render_knowledge_block"]
