import unittest
from infrastructure.stages.requirements.tree import RequirementTree
from infrastructure.stages.design.groups import requirement_groups
from design_fixtures import lite_tree

def node(rid, children=()):
    return {"id": rid, "name": rid, "description": "", "scenarios": [], "children": list(children)}

class Groups(unittest.TestCase):
    def test_lite_has_three_groups(self):
        self.assertEqual(requirement_groups(lite_tree()), [
            {"id": "REQ-1", "part": 1, "leaves": ["REQ-1.1"]},
            {"id": "REQ-2", "part": 1, "leaves": ["REQ-2.1", "REQ-2.2", "REQ-2.3", "REQ-2.4", "REQ-2.5"]},
            {"id": "REQ-3", "part": 1, "leaves": ["REQ-3.1", "REQ-3.2"]}])

    def test_large_group_is_split_into_parts_of_eight(self):
        tree = RequirementTree(node("ROOT", [node("A", [node(f"A.{i}") for i in range(1, 20)])]))
        groups = requirement_groups(tree)
        self.assertEqual([(g["id"], g["part"], len(g["leaves"])) for g in groups], [("A", 1, 8), ("A", 2, 8), ("A", 3, 3)])
        self.assertEqual([leaf for g in groups for leaf in g["leaves"]], tree.leaves)

    def test_leaf_directly_under_the_root_and_nested_folders(self):
        tree = RequirementTree(node("ROOT", [node("A"), node("B", [node("B.1", [node("B.1.1"), node("B.1.2")])])]))
        self.assertEqual(requirement_groups(tree), [
            {"id": "A", "part": 1, "leaves": ["A"]}, {"id": "B", "part": 1, "leaves": ["B.1.1", "B.1.2"]}])

    def test_single_requirement_tree(self):
        self.assertEqual(requirement_groups(RequirementTree(node("ROOT"))), [{"id": "ROOT", "part": 1, "leaves": ["ROOT"]}])
