def test_models_import():
    from app.models import Matter, Document, AnalysisResult, License
    assert Matter.__tablename__ == "matters"
    assert Document.__tablename__ == "documents"
    assert AnalysisResult.__tablename__ == "analysis_results"
    assert License.__tablename__ == "licenses"
