#!/usr/bin/env python
"""Test script to verify prompts are loading from database"""

from utils.prompt_config import (
    build_llm_system_prompt,
    LLM_NODE_SYSTEM_PROMPT
)

print("=" * 80)
print("TESTING PROMPT LOADING FROM DATABASE")
print("=" * 80)

# Test 1: Build LLM prompt
print("\n1. Building LLM system prompt...")
llm_prompt = build_llm_system_prompt()
print(f"   Generated {len(llm_prompt)} characters")
print(f"   First 200 chars:\n   {llm_prompt[:200]}...")

# Test 2: Check module-level variable
print("\n2. Checking LLM_NODE_SYSTEM_PROMPT...")
print(f"   LLM_NODE_SYSTEM_PROMPT length: {len(LLM_NODE_SYSTEM_PROMPT)} chars")
if len(LLM_NODE_SYSTEM_PROMPT) > 0:
    print("   Prompts loaded successfully!")
else:
    print("   Prompts may not have loaded correctly")

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)
