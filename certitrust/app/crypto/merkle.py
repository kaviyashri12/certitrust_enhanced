import hashlib
from typing import List, Tuple

def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def build_merkle_tree(leaves: List[str]) -> Tuple[str, List[List[str]]]:
    """
    Returns (root_hash, levels)
    levels[0] = leaf hashes, levels[-1] = [root]
    """
    if not leaves:
        raise ValueError("No leaves provided")

    levels = [leaves[:]]  # level 0 = leaves

    current = leaves[:]
    while len(current) > 1:
        next_level = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else left  # duplicate last if odd
            parent = sha256_hex(left + right)
            next_level.append(parent)
        levels.append(next_level)
        current = next_level

    root = current[0]
    return root, levels