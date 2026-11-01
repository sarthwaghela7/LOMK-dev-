import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_auto_flint_and_flintdesk_llm_nodes_are_separate():
    auto_llm = importlib.import_module('services.Processing_pipeline.llm_node')
    fd_llm = importlib.import_module('services.Processing_pipeline.fd_llm_node')

    assert hasattr(auto_llm, 'LLM')
    assert hasattr(fd_llm, 'FDLLM')
    assert auto_llm.LLM is not fd_llm.FDLLM


def test_f3_processor_routes_to_the_correct_llm_node():
    f3_module = importlib.import_module('services.Database_sync.f3')
    auto_llm = importlib.import_module('services.Processing_pipeline.llm_node')
    fd_llm = importlib.import_module('services.Processing_pipeline.fd_llm_node')

    autoflint_processor = f3_module.F3Processor(application='autoflint')
    flintdesk_processor = f3_module.F3Processor(application='flintdesk')

    assert isinstance(autoflint_processor.llm, auto_llm.LLM)
    assert isinstance(flintdesk_processor.llm, fd_llm.FDLLM)
