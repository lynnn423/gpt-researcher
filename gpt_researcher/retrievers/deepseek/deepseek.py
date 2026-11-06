"""DeepSeek Search API retriever for GPT Researcher."""

import os
from typing import Any, Dict, List, Optional

import requests


class DeepSeekSearch:
    """Search via DeepSeek's in-house Search API."""

    def __init__(self, query, headers=None, topic="general", query_domains=None):
        self.query = query
        self.headers = headers or {}
        self.topic = topic
        self.base_url = "https://api.deepseek.com/v1/tools/search"
        self.api_key = self.headers.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        self.query_domains = query_domains or None
        if not self.api_key:
            print("DeepSeek API key not found; set DEEPSEEK_API_KEY.")

    def search(self, max_results: int = 10) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []
        query = (self.query or "").strip()
        if not query:
            return []
        if self.query_domains:
            sites = " OR ".join(f"site:{domain}" for domain in self.query_domains)
            query = f"{query} {sites}"
        payload = {
            "query": query[:128],
            "count": max(1, min(int(max_results), 50)),
            "need_content": True,
        }
        response = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
        normalized = []
        for item in results:
            url = item.get("url")
            if not url:
                continue
            content = item.get("content") or item.get("summary") or item.get("snippet") or ""
            normalized.append({
                "url": url,
                "raw_content": content,
                "title": item.get("title") or "",
                "score": item.get("rerank_score"),
                "published_date": item.get("date"),
            })
        return normalized
