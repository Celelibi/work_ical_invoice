"""This module implements approximate matching between strings.

This matching is based on the levenshtein distance and is either between two
strings or between one string against a collection of strings.

The approximate match doesn't care about the word order or incompleteness. See
function docstring for more information.
"""


def levenshtein(s1, s2):
    """Levenshtein distance between two strings."""

    prevrow = list(range(len(s2) + 1))

    for i1, c1 in enumerate(s1, 1):
        row = [i1]
        for i2, c2 in enumerate(s2, 1):
            matchscore = 0 if c1 == c2 else 2
            row.append(min(row[-1] + 1, prevrow[i2] + 1, prevrow[i2 - 1] + matchscore))
        prevrow = row

    return prevrow[-1]



def approx_score(s1, s2):
    """Score of "likeness" of two strings.

    This scoring function tries to use a bag-of-word model where the order of
    the words doesn't matter.
    For each word of the first string, it finds the best match among the words
    of the second string.
    The final score is the sum of all the best-match scores.
    """

    l1 = s1.lower().split()
    l2 = s2.lower().split()
    return sum(min(levenshtein(w1, w2) for w2 in l2) for w1 in l1)



def approx_match(nail, haystack):
    """This function looks for a nail (not quite a needle) in a haystack. It
    returns the matching needle."""

    return min(haystack, key=lambda hay: approx_score(nail, hay))
