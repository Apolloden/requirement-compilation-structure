"""Split the leaf requirements into groups for the plan-stage workers.

A group is one child of the root with every leaf below it, so related requirements (a create
form and its edit form, say) are designed in one call. A large group is cut into parts so
one answer stays small."""


def requirement_groups(tree, max_leaves: int = 8) -> list[dict]:
    """[{"id", "part", "leaves"}] covering every leaf exactly once, in tree.leaves order."""
    root = tree.postorder[-1]
    children = tree.children[root]
    if not children:
        return [{"id": root, "part": 1, "leaves": [root]}]
    groups = []
    for child in children:
        below = leaves_below(tree, child)
        leaves = [leaf for leaf in tree.leaves if leaf in below]
        for index in range(0, len(leaves), max_leaves):
            groups.append({"id": child, "part": index // max_leaves + 1, "leaves": leaves[index:index + max_leaves]})
    return groups


def leaves_below(tree, rid) -> set[str]:
    """The leaf IDs at or below one requirement."""
    if not tree.children[rid]:
        return {rid}
    return set().union(*(leaves_below(tree, child) for child in tree.children[rid]))
