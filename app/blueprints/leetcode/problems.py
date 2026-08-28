"""The LeetCode "Top Interview 150" study plan, as its 23 topic groups.

Pulled verbatim from LeetCode's own study-plan GraphQL endpoint
(`studyPlanV2Detail(planSlug: "top-interview-150")`) so the names, slugs,
ordering, and difficulties match the official plan at
https://leetcode.com/studyplan/top-interview-150/ . Each problem's page is
`https://leetcode.com/problems/<slug>/` -- see `problem_url()`.

This is static reference data with no runtime state: the tracker's
per-problem "did I get it" marks live entirely in the visitor's browser
(localStorage, keyed by slug), never here or in the database.
"""

# (title, slug, difficulty) per problem. Difficulty is one of
# "Easy" / "Medium" / "Hard" -- display-only, not used for logic.
TOPICS: list[dict] = [
    {
        "name": "Array / String",
        "problems": [
            ("Merge Sorted Array", "merge-sorted-array", "Easy"),
            ("Remove Element", "remove-element", "Easy"),
            ("Remove Duplicates from Sorted Array", "remove-duplicates-from-sorted-array", "Easy"),
            ("Remove Duplicates from Sorted Array II", "remove-duplicates-from-sorted-array-ii", "Medium"),
            ("Majority Element", "majority-element", "Easy"),
            ("Rotate Array", "rotate-array", "Medium"),
            ("Best Time to Buy and Sell Stock", "best-time-to-buy-and-sell-stock", "Easy"),
            ("Best Time to Buy and Sell Stock II", "best-time-to-buy-and-sell-stock-ii", "Medium"),
            ("Jump Game", "jump-game", "Medium"),
            ("Jump Game II", "jump-game-ii", "Medium"),
            ("H-Index", "h-index", "Medium"),
            ("Insert Delete GetRandom O(1)", "insert-delete-getrandom-o1", "Medium"),
            ("Product of Array Except Self", "product-of-array-except-self", "Medium"),
            ("Gas Station", "gas-station", "Medium"),
            ("Candy", "candy", "Hard"),
            ("Trapping Rain Water", "trapping-rain-water", "Hard"),
            ("Roman to Integer", "roman-to-integer", "Easy"),
            ("Integer to Roman", "integer-to-roman", "Medium"),
            ("Length of Last Word", "length-of-last-word", "Easy"),
            ("Longest Common Prefix", "longest-common-prefix", "Easy"),
            ("Reverse Words in a String", "reverse-words-in-a-string", "Medium"),
            ("Zigzag Conversion", "zigzag-conversion", "Medium"),
            (
                "Find the Index of the First Occurrence in a String",
                "find-the-index-of-the-first-occurrence-in-a-string",
                "Easy",
            ),
            ("Text Justification", "text-justification", "Hard"),
        ],
    },
    {
        "name": "Two Pointers",
        "problems": [
            ("Valid Palindrome", "valid-palindrome", "Easy"),
            ("Is Subsequence", "is-subsequence", "Easy"),
            ("Two Sum II - Input Array Is Sorted", "two-sum-ii-input-array-is-sorted", "Medium"),
            ("Container With Most Water", "container-with-most-water", "Medium"),
            ("3Sum", "3sum", "Medium"),
        ],
    },
    {
        "name": "Sliding Window",
        "problems": [
            ("Minimum Size Subarray Sum", "minimum-size-subarray-sum", "Medium"),
            (
                "Longest Substring Without Repeating Characters",
                "longest-substring-without-repeating-characters",
                "Medium",
            ),
            (
                "Substring with Concatenation of All Words",
                "substring-with-concatenation-of-all-words",
                "Hard",
            ),
            ("Minimum Window Substring", "minimum-window-substring", "Hard"),
        ],
    },
    {
        "name": "Matrix",
        "problems": [
            ("Valid Sudoku", "valid-sudoku", "Medium"),
            ("Spiral Matrix", "spiral-matrix", "Medium"),
            ("Rotate Image", "rotate-image", "Medium"),
            ("Set Matrix Zeroes", "set-matrix-zeroes", "Medium"),
            ("Game of Life", "game-of-life", "Medium"),
        ],
    },
    {
        "name": "Hashmap",
        "problems": [
            ("Ransom Note", "ransom-note", "Easy"),
            ("Isomorphic Strings", "isomorphic-strings", "Easy"),
            ("Word Pattern", "word-pattern", "Easy"),
            ("Valid Anagram", "valid-anagram", "Easy"),
            ("Group Anagrams", "group-anagrams", "Medium"),
            ("Two Sum", "two-sum", "Easy"),
            ("Happy Number", "happy-number", "Easy"),
            ("Contains Duplicate II", "contains-duplicate-ii", "Easy"),
            ("Longest Consecutive Sequence", "longest-consecutive-sequence", "Medium"),
        ],
    },
    {
        "name": "Intervals",
        "problems": [
            ("Summary Ranges", "summary-ranges", "Easy"),
            ("Merge Intervals", "merge-intervals", "Medium"),
            ("Insert Interval", "insert-interval", "Medium"),
            (
                "Minimum Number of Arrows to Burst Balloons",
                "minimum-number-of-arrows-to-burst-balloons",
                "Medium",
            ),
        ],
    },
    {
        "name": "Stack",
        "problems": [
            ("Valid Parentheses", "valid-parentheses", "Easy"),
            ("Simplify Path", "simplify-path", "Medium"),
            ("Min Stack", "min-stack", "Medium"),
            ("Evaluate Reverse Polish Notation", "evaluate-reverse-polish-notation", "Medium"),
            ("Basic Calculator", "basic-calculator", "Hard"),
        ],
    },
    {
        "name": "Linked List",
        "problems": [
            ("Linked List Cycle", "linked-list-cycle", "Easy"),
            ("Add Two Numbers", "add-two-numbers", "Medium"),
            ("Merge Two Sorted Lists", "merge-two-sorted-lists", "Easy"),
            ("Copy List with Random Pointer", "copy-list-with-random-pointer", "Medium"),
            ("Reverse Linked List II", "reverse-linked-list-ii", "Medium"),
            ("Reverse Nodes in k-Group", "reverse-nodes-in-k-group", "Hard"),
            ("Remove Nth Node From End of List", "remove-nth-node-from-end-of-list", "Medium"),
            ("Remove Duplicates from Sorted List II", "remove-duplicates-from-sorted-list-ii", "Medium"),
            ("Rotate List", "rotate-list", "Medium"),
            ("Partition List", "partition-list", "Medium"),
            ("LRU Cache", "lru-cache", "Medium"),
        ],
    },
    {
        "name": "Binary Tree General",
        "problems": [
            ("Maximum Depth of Binary Tree", "maximum-depth-of-binary-tree", "Easy"),
            ("Same Tree", "same-tree", "Easy"),
            ("Invert Binary Tree", "invert-binary-tree", "Easy"),
            ("Symmetric Tree", "symmetric-tree", "Easy"),
            (
                "Construct Binary Tree from Preorder and Inorder Traversal",
                "construct-binary-tree-from-preorder-and-inorder-traversal",
                "Medium",
            ),
            (
                "Construct Binary Tree from Inorder and Postorder Traversal",
                "construct-binary-tree-from-inorder-and-postorder-traversal",
                "Medium",
            ),
            (
                "Populating Next Right Pointers in Each Node II",
                "populating-next-right-pointers-in-each-node-ii",
                "Medium",
            ),
            ("Flatten Binary Tree to Linked List", "flatten-binary-tree-to-linked-list", "Medium"),
            ("Path Sum", "path-sum", "Easy"),
            ("Sum Root to Leaf Numbers", "sum-root-to-leaf-numbers", "Medium"),
            ("Binary Tree Maximum Path Sum", "binary-tree-maximum-path-sum", "Hard"),
            ("Binary Search Tree Iterator", "binary-search-tree-iterator", "Medium"),
            ("Count Complete Tree Nodes", "count-complete-tree-nodes", "Medium"),
            (
                "Lowest Common Ancestor of a Binary Tree",
                "lowest-common-ancestor-of-a-binary-tree",
                "Medium",
            ),
        ],
    },
    {
        "name": "Binary Tree BFS",
        "problems": [
            ("Binary Tree Right Side View", "binary-tree-right-side-view", "Medium"),
            ("Average of Levels in Binary Tree", "average-of-levels-in-binary-tree", "Easy"),
            ("Binary Tree Level Order Traversal", "binary-tree-level-order-traversal", "Medium"),
            (
                "Binary Tree Zigzag Level Order Traversal",
                "binary-tree-zigzag-level-order-traversal",
                "Medium",
            ),
        ],
    },
    {
        "name": "Binary Search Tree",
        "problems": [
            ("Minimum Absolute Difference in BST", "minimum-absolute-difference-in-bst", "Easy"),
            ("Kth Smallest Element in a BST", "kth-smallest-element-in-a-bst", "Medium"),
            ("Validate Binary Search Tree", "validate-binary-search-tree", "Medium"),
        ],
    },
    {
        "name": "Graph General",
        "problems": [
            ("Number of Islands", "number-of-islands", "Medium"),
            ("Surrounded Regions", "surrounded-regions", "Medium"),
            ("Clone Graph", "clone-graph", "Medium"),
            ("Evaluate Division", "evaluate-division", "Medium"),
            ("Course Schedule", "course-schedule", "Medium"),
            ("Course Schedule II", "course-schedule-ii", "Medium"),
        ],
    },
    {
        "name": "Graph BFS",
        "problems": [
            ("Snakes and Ladders", "snakes-and-ladders", "Medium"),
            ("Minimum Genetic Mutation", "minimum-genetic-mutation", "Medium"),
            ("Word Ladder", "word-ladder", "Hard"),
        ],
    },
    {
        "name": "Trie",
        "problems": [
            ("Implement Trie (Prefix Tree)", "implement-trie-prefix-tree", "Medium"),
            (
                "Design Add and Search Words Data Structure",
                "design-add-and-search-words-data-structure",
                "Medium",
            ),
            ("Word Search II", "word-search-ii", "Hard"),
        ],
    },
    {
        "name": "Backtracking",
        "problems": [
            (
                "Letter Combinations of a Phone Number",
                "letter-combinations-of-a-phone-number",
                "Medium",
            ),
            ("Combinations", "combinations", "Medium"),
            ("Permutations", "permutations", "Medium"),
            ("Combination Sum", "combination-sum", "Medium"),
            ("N-Queens II", "n-queens-ii", "Hard"),
            ("Generate Parentheses", "generate-parentheses", "Medium"),
            ("Word Search", "word-search", "Medium"),
        ],
    },
    {
        "name": "Divide & Conquer",
        "problems": [
            (
                "Convert Sorted Array to Binary Search Tree",
                "convert-sorted-array-to-binary-search-tree",
                "Easy",
            ),
            ("Sort List", "sort-list", "Medium"),
            ("Construct Quad Tree", "construct-quad-tree", "Medium"),
            ("Merge k Sorted Lists", "merge-k-sorted-lists", "Hard"),
        ],
    },
    {
        "name": "Kadane's Algorithm",
        "problems": [
            ("Maximum Subarray", "maximum-subarray", "Medium"),
            ("Maximum Sum Circular Subarray", "maximum-sum-circular-subarray", "Medium"),
        ],
    },
    {
        "name": "Binary Search",
        "problems": [
            ("Search Insert Position", "search-insert-position", "Easy"),
            ("Search a 2D Matrix", "search-a-2d-matrix", "Medium"),
            ("Find Peak Element", "find-peak-element", "Medium"),
            ("Search in Rotated Sorted Array", "search-in-rotated-sorted-array", "Medium"),
            (
                "Find First and Last Position of Element in Sorted Array",
                "find-first-and-last-position-of-element-in-sorted-array",
                "Medium",
            ),
            (
                "Find Minimum in Rotated Sorted Array",
                "find-minimum-in-rotated-sorted-array",
                "Medium",
            ),
            ("Median of Two Sorted Arrays", "median-of-two-sorted-arrays", "Hard"),
        ],
    },
    {
        "name": "Heap",
        "problems": [
            ("Kth Largest Element in an Array", "kth-largest-element-in-an-array", "Medium"),
            ("IPO", "ipo", "Hard"),
            ("Find K Pairs with Smallest Sums", "find-k-pairs-with-smallest-sums", "Medium"),
            ("Find Median from Data Stream", "find-median-from-data-stream", "Hard"),
        ],
    },
    {
        "name": "Bit Manipulation",
        "problems": [
            ("Add Binary", "add-binary", "Easy"),
            ("Reverse Bits", "reverse-bits", "Easy"),
            ("Number of 1 Bits", "number-of-1-bits", "Easy"),
            ("Single Number", "single-number", "Easy"),
            ("Single Number II", "single-number-ii", "Medium"),
            ("Bitwise AND of Numbers Range", "bitwise-and-of-numbers-range", "Medium"),
        ],
    },
    {
        "name": "Math",
        "problems": [
            ("Palindrome Number", "palindrome-number", "Easy"),
            ("Plus One", "plus-one", "Easy"),
            ("Factorial Trailing Zeroes", "factorial-trailing-zeroes", "Medium"),
            ("Sqrt(x)", "sqrtx", "Easy"),
            ("Pow(x, n)", "powx-n", "Medium"),
            ("Max Points on a Line", "max-points-on-a-line", "Hard"),
        ],
    },
    {
        "name": "1D DP",
        "problems": [
            ("Climbing Stairs", "climbing-stairs", "Easy"),
            ("House Robber", "house-robber", "Medium"),
            ("Word Break", "word-break", "Medium"),
            ("Coin Change", "coin-change", "Medium"),
            ("Longest Increasing Subsequence", "longest-increasing-subsequence", "Medium"),
        ],
    },
    {
        "name": "Multidimensional DP",
        "problems": [
            ("Triangle", "triangle", "Medium"),
            ("Minimum Path Sum", "minimum-path-sum", "Medium"),
            ("Unique Paths II", "unique-paths-ii", "Medium"),
            ("Longest Palindromic Substring", "longest-palindromic-substring", "Medium"),
            ("Interleaving String", "interleaving-string", "Medium"),
            ("Edit Distance", "edit-distance", "Medium"),
            (
                "Best Time to Buy and Sell Stock III",
                "best-time-to-buy-and-sell-stock-iii",
                "Hard",
            ),
            (
                "Best Time to Buy and Sell Stock IV",
                "best-time-to-buy-and-sell-stock-iv",
                "Hard",
            ),
            ("Maximal Square", "maximal-square", "Medium"),
        ],
    },
]

PROBLEM_URL_TEMPLATE = "https://leetcode.com/problems/{slug}/"


def problem_url(slug: str) -> str:
    return PROBLEM_URL_TEMPLATE.format(slug=slug)


def iter_problems():
    """Yield every problem across all topics as a flat sequence of dicts."""
    for topic in TOPICS:
        for title, slug, difficulty in topic["problems"]:
            yield {
                "title": title,
                "slug": slug,
                "difficulty": difficulty,
                "url": problem_url(slug),
                "topic": topic["name"],
            }


def topic_view():
    """TOPICS shaped for the template: tuples expanded into dicts with URLs."""
    return [
        {
            "name": topic["name"],
            "problems": [
                {
                    "title": title,
                    "slug": slug,
                    "difficulty": difficulty,
                    "url": problem_url(slug),
                }
                for title, slug, difficulty in topic["problems"]
            ],
        }
        for topic in TOPICS
    ]


TOTAL = sum(len(topic["problems"]) for topic in TOPICS)

# The plan is exactly 150 problems -- a mismatch means an accidental edit
# to the tables above, so fail loudly at import rather than silently
# shipping a short list.
assert TOTAL == 150, f"Top Interview 150 must have 150 problems, found {TOTAL}"

_SLUGS = [p["slug"] for p in iter_problems()]
assert len(_SLUGS) == len(set(_SLUGS)), "duplicate slug in the Top Interview 150 tables"
