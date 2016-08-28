#!/bin/sh
curl "localhost:9200/lagen/_search?search_type=count&pretty=true" -d '{"aggs": {"count_by_type": {"terms": {"field": "_type"}}}}'
