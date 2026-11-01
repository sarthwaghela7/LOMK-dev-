"""
Utility functions for call_id formatting.
Converts call_number to AF#N format (AF#1, AF#2, etc.)
"""

def format_call_id(call_number: int | None) -> str:
    """
    Format call_number as AF#N
    
    Args:
        call_number: Integer call number (1, 2, 3, ...)
        
    Returns:
        Formatted string like "AF#1", "AF#2"
    """
    if call_number is None:
        return "AF#Unknown"
    return f"AF#{call_number}"


def extract_call_number(formatted_id: str) -> int | None:
    """
    Extract call_number from formatted ID like "AF#5"
    
    Args:
        formatted_id: String like "AF#5"
        
    Returns:
        Integer call_number or None
    """
    if not formatted_id:
        return None
    try:
        # Remove "AF#" prefix and convert to int
        return int(formatted_id.replace("AF#", ""))
    except (ValueError, AttributeError):
        return None
