"""Discovery: find candidate dark-web pages for a query, before the pipeline.

SentinelX's scrapers process forums you already know about. This package is
the front half that answers "where should I look?": a query goes to one or
more search engines, results come back as candidate pages, and selected pages
are fed into the existing pipeline through the generic page reader.

    query -> (optional LLM refinement) -> engines -> ranked candidates
          -> analyst selects -> page_reader -> raw_posts -> extract/LLM/MITRE
"""
