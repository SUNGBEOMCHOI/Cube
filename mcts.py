import logging
import math

import numpy as np

EPS = 1e-8

log = logging.getLogger(__name__)


class MCTS():
    """
    This class handles the MCTS tree.
    """

    def __init__(self, game, model, cfg):

        """
        game : cube class
        model : trained neural network
        cfg : configuration(numMCTSSims, cpuct)
        """

        self.game = game
        self.model = model
        self.numMCTSSim = cfg['mcts']['numMCTSSim']
        self.cpuct = cfg['mcts']['cpuct']
        self.action_dim = 6

        self.Qsa = {}  # stores Q values for s,a (as defined in the paper)
        self.Nsa = {}  # stores #times edge s,a was visited
        self.Ns = {}  # stores #times board s was visited
        self.Ps = {}  # stores initial policy (returned by neural net)

        self.Es = {}  # stores game.getGameEnded ended for board s

    def getActionProb(self, canonicalBoard, temp=0):
        """
        This function performs numMCTSSims simulations of MCTS starting from
        canonicalBoard.
        Args:
            canonicalBoard : numpy array which represent state  (7,21)

        Returns:
            probs: a policy vector where the probability of the ith action is
                   proportional to Nsa[(s,a)]**(1./temp)   , list type
        """

        # numMCTSSims : number of MCTS simulations
        for i in range(self.numMCTSSim):
            self.search(canonicalBoard)

        s = np.array2string(canonicalBoard)
        counts = [self.Nsa[(s, a)] if (s, a) in self.Nsa else 0 for a in range(self.action_dim)] #(6,) list  number of times that (s,a) was visited

        
        if temp == 0:
            #greedy
            bestAs = np.array(np.argwhere(counts == np.max(counts))).flatten()
            bestA = np.random.choice(bestAs)
            probs = [0] * len(counts)
            probs[bestA] = 1
            return probs

        counts = [x ** (1. / temp) for x in counts]
        counts_sum = float(sum(counts))
        probs = [x / counts_sum for x in counts]
        return probs

    def search(self, canonicalBoard, game_ended = False):
        """
        This function performs one iteration of MCTS. It is recursively called
        till a leaf node is found. The action chosen at each node is one that
        has the maximum upper confidence bound as in the paper.

        Once a leaf node is found, the neural network is called to return an
        initial policy P and a value v for the state. This value is propagated
        up the search path. In case the leaf node is a terminal state, the
        outcome is propagated up the search path. The values of Ns, Nsa, Qsa are
        updated.

        NOTE: the return values are the negative of the value of the current
        state. This is done since v is in [-1,1] and if v is the value of a
        state for the current player, then its value is -v for the other player.

        Args:
            canonicalBoard : numpy array which represent state  (7,21)
            game_ended : Boolean type

        Returns:
            v: the negative of the value of the current canonicalBoard (if canonicalBoard is terminal, return 1)
        """

        s = np.array2string(canonicalBoard)

        if s not in self.Es:
            self.Es[s] = game_ended

        if self.Es[s]:
            # terminal node is found
            return 1


        if s not in self.Ps:
            # leaf node is found
            v, self.Ps[s] = self.model.predict(canonicalBoard)

            self.Ns[s] = 0
            return -v

        cur_best = -float('inf')
        best_act = -1

        # pick the action with the highest upper confidence bound
        for a in range(self.action_dim):
            if (s, a) in self.Qsa:
                u = self.Qsa[(s, a)] + self.cpuct * self.Ps[s][a] * math.sqrt(self.Ns[s]) / (
                        1 + self.Nsa[(s, a)])
            else:
                u = self.cpuct * self.Ps[s][a] * math.sqrt(self.Ns[s] + EPS)  # Q = 0 ?

            if u > cur_best:
                cur_best = u
                best_act = a

        a = best_act
        next_s, _, done, _ = self.game.step(a)

        #next_s,  next_player = self.game.step(a)

        v = self.search(next_s, game_ended=done)

        if (s, a) in self.Qsa:
            self.Qsa[(s, a)] = (self.Nsa[(s, a)] * self.Qsa[(s, a)] + v) / (self.Nsa[(s, a)] + 1)
            self.Nsa[(s, a)] += 1

        else:
            self.Qsa[(s, a)] = v
            self.Nsa[(s, a)] = 1

        self.Ns[s] += 1
        return -v
