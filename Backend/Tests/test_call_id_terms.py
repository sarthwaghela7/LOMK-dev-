from services.analysis_read_query import MAX_CALL_ID_TERMS, call_id_terms


def test_single_id_is_unchanged():
    assert call_id_terms("  AF#123 ") == ["af#123"]
    assert call_id_terms("1234-abcd") == ["1234-abcd"]


def test_several_ids_split_on_commas_and_ignore_blanks():
    assert call_id_terms("AA-1, bb-2,,  ,CC-3") == ["aa-1", "bb-2", "cc-3"]


def test_empty_or_missing_means_no_filter():
    assert call_id_terms(None) == [] and call_id_terms("") == [] and call_id_terms(" , ") == []


def test_term_count_is_capped():
    assert len(call_id_terms(",".join(str(n) for n in range(500)))) == MAX_CALL_ID_TERMS
