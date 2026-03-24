"""
Jina Reranker Integration.
Calls the Jina AI API to re-rank code chunks based on semantic relevance to the bug report.
"""

import json
import logging
import requests
from typing import List, Dict, Any

from config import config

logger = logging.getLogger(__name__)


class JinaReranker:
    def __init__(self):
        self.api_key = config.reranker.api_key
        self.model = config.reranker.model_name
        self.url = config.reranker.base_url
        self.enabled = config.reranker.enabled and bool(self.api_key)

    def rerank(self, query: str, documents: List[str], top_n: int = None) -> List[Dict[str, Any]]:
        """
        Reranks a list of documents based on the query.
        Returns a list of dicts: {"index": original_index, "document": {"text": ...}, "relevance_score": float}
        """
        if not documents:
            return []
            
        top_n = top_n or config.reranker.top_n

        if not self.enabled:
            logger.warning("Jina Reranker is disabled or API key is missing. Skipping reranking.")
            return [
                {"index": i, "document": {"text": doc}, "relevance_score": 1.0 - (i * 0.01)} 
                for i, doc in enumerate(documents[:top_n])
            ]

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # Avoid payload too large errors by truncating very large code chunks.
        safe_documents = [doc[:20000] for doc in documents] 

        payload = {
            "model": self.model,
            "query": query[:10000],  # Truncate query if extremely long
            "documents": safe_documents,
            "top_n": min(top_n, len(documents))
        }

        logger.info(f"Calling Jina Reranker ({self.model}) with {len(documents)} documents...")

        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.info(f"Jina Reranker completed successfully. Returned {len(results)} results.")
            return results
        except requests.exceptions.RequestException as e:
            logger.error(f"Jina Reranker API error: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            
            # Fallback to original order
            logger.warning("Falling back to original document order due to API error.")
            return [
                {"index": i, "document": {"text": doc}, "relevance_score": 0.0} 
                for i, doc in enumerate(documents[:top_n])
            ]

