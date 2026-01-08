"""This module implements approximate matching between strings.

This matching is based on the levenshtein distance and is either between two
strings or between one string against a collection of strings.

The approximate match doesn't care about the word order or incompleteness. See
function docstring for more information.
"""

import re



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



def _greedy_multimatch(s1, s2):
    """This function represents the strings as Bag-of-Words and match each word
    of s1 with the closest word of s2 w.r.t levenshtein edit distance. Words of
    s2 can be matched with several words of s1.
    The final score is the sum of all the matched edit distances."""

    l1 = re.sub(r'\W+', " ", s1.lower()).split()
    l2 = re.sub(r'\W+', " ", s2.lower()).split()
    return sum(min(levenshtein(w1, w2) for w2 in l2) for w1 in l1)



def _greedy_multimatch2(s1, s2):
    """This function represents the strings as Bag-of-Words and match each word
    of s1 with the closest word of s2 w.r.t levenshtein edit distance. First
    the closest words are matched and removed from the set of words. Then this
    is repeated as long as there are words in either bag.
    The final score is the sum of all the matched edit distances."""

    l1 = re.sub(r'\W+', " ", s1.lower()).split()
    l2 = re.sub(r'\W+', " ", s2.lower()).split()

    # Distance matrix is represented as a list of tuple (distance, y, x)
    dists = [(levenshtein(w1, w2), i, j) for i, w1 in enumerate(l1) for j, w2 in enumerate(l2)]
    dists.sort()

    # Words are "removed" from the bags by ignoring their rows and columns in
    # the distance matrix
    done1 = set()
    done2 = set()
    total = 0

    for d, i, j in dists:
        if i in done1 or j in done2:
            continue

        total += d
        done1.add(i)
        done2.add(j)

    return total + abs(len(l1) - len(l2))
    return total, abs(len(l1) - len(l2))



def approx_score(s1, s2):
    """Score of "likeness" of two strings.

    This scoring function tries to use a bag-of-word model where the order of
    the words doesn't matter.
    For each word of the first string, it finds the best match among the words
    of the second string.
    The final score is the sum of all the best-match scores.
    """
    return _greedy_multimatch2(s1, s2)




def approx_match(nail, haystack, key=None):
    """This function looks for a nail (not quite a needle) in a haystack. It
    returns the matching needle."""

    if key is None:
        key = lambda x: x
    return min(haystack, key=lambda hay: approx_score(nail, key(hay)))
