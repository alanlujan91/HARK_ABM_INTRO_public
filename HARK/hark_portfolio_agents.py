import HARK.ConsumptionSaving.ConsPortfolioModel as cpm
import HARK.ConsumptionSaving.ConsIndShockModel as cism
from HARK.core import distribute_params
from HARK.distribution import Uniform
import math
import numpy as np
import pandas as pd
from statistics import mean


import sys

sys.path.append('.')
## TODO configuration file for this value!
sys.path.append('../PNL/py')

import util as UTIL
import pnl as pnl

import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


### Initializing agents

def update_return(dict1, dict2):
    """
    Returns new dictionary,
    copying dict1 and updating the values of dict2
    """
    dict3 = dict1.copy()
    dict3.update(dict2)

    return dict3

def distribute(agents, dist_params):
    """
    Distribue the discount rate among a set of agents according
    the distribution from Carroll et al., "Distribution of Wealth"
    paper.

    Parameters
    ----------

    agents: list of AgentType
        A list of AgentType

    dist_params:

    Returns
    -------
        agents: A list of AgentType
    """
 
    # This is hacky. Should streamline this in HARK.

    for param in dist_params:
        agents_distributed = [
            distribute_params(
                agent,
                param,
                dist_params[param]['n'],
                Uniform(
                    bot=dist_params[param]['bot'],
                    top=dist_params[param]['top']
                )
            )
            for agent in agents
        ]

        agents = [
            agent
            for agent_dist in agents_distributed
            for agent in agent_dist
        ]

        # should be unecessary but a hack to cover a HARK bug
        # https://github.com/econ-ark/HARK/issues/994
        for agent in agents:
            agent.assign_parameters(**{param : getattr(agent, param)})

    return agents

def create_distributed_agents(agent_parameters, dist_params, n_per_class):
    """
    Creates agents of the given classes with stable parameters.
    Will overwrite the DiscFac with a distribution from CSTW_MPC.

    Parameters
    ----------
    agent_parameters: dict
        Parameters shared by all agents (unless overwritten).

    dist_params: dict of dicts
        Parameters to distribute agents over, with discrete Uniform arguments

    n_per_class: int
        number of agents to instantiate per class
    """
    num_classes = math.prod([dist_params[dp]['n'] for dp in dist_params])
    agent_batches = [{'AgentCount' : num_classes}] * n_per_class

    agents = [
        cpm.PortfolioConsumerType(
            **update_return(agent_parameters, ac)
        )
        for ac
        in agent_batches
    ]

    agents = distribute(agents, dist_params)

    return agents

def init_simulations(agents):
    """
    Sets up the agents with their state for the state of the simulation
    """
    for agent in agents:
        agent.track_vars += ['pLvl','mNrm','cNrm','Share','Risky']

        agent.assign_parameters(AdjustPrb = 1.0)
        agent.T_sim = 1000 # arbitrary!
        agent.solve()

        ### TODO: make and equivalent PF model and solve it
        ### to get the steady-state wealth
        ### This code block is not working.
        #pf_clone = cism.PerfForesightConsumerType(**agent.parameters)
        #pf_clone.assign_parameters(Rfree = pf_clone.parameters['RiskyAvg'])
        #pf_clone.solve()
        #
        #if not hasattr(pf_clone.solution[0],'mNrmStE'):
        #    # See https://github.com/econ-ark/HARK/issues/1005
        #    x = 0.7
        #    print(f"Agent has no steady state normalized market resources. Using {x} as stopgap.")
        #    pf_clone.solution[0].mNrmStE = x # A hack.

        agent.initialize_sim()

        # set normalize assets to steady state market resources.
        agent.state_now['mNrm'][:] = 1.0 #pf_clone.solution[0].mNrmStE
        agent.state_now['aNrm'] = agent.state_now['mNrm'] - agent.solution[0].cFuncAdj(agent.state_now['mNrm'])
        agent.state_now['aLvl'] = agent.state_now['aNrm'] * agent.state_now['pLvl']

    return agents


######
#   Math Utilities
######

def ror_quarterly(ror, n_q):
    """
    Convert a daily rate of return to a rate for (n_q) days.
    """
    return pow(1 + ror, n_q) - 1

def sig_quarterly(std, n_q):
    """
    Convert a daily standard deviation to a standard deviation for (n_q) days.

    This formula only holds for special cases (see paper by Andrew Lo), 
    but since we are generating the data we meet this special case.
    """
    return math.sqrt(n_q) * std

def lognormal_moments_to_normal(mu_x, std_x):
    """
    Given a mean and standard deviation of a lognormal distribution,
    return the corresponding mu and sigma for the distribution.
    (That is, the mean and standard deviation of the "underlying"
    normal distribution.)
    """
    mu = np.log(mu_x ** 2 / math.sqrt(mu_x ** 2 + std_x ** 2))

    sigma = math.sqrt(np.log(1 + std_x ** 2 / mu_x ** 2))

    return mu, sigma

def combine_lognormal_rates(ror1, std1, ror2, std2):
    """
    Given two mean rates of return and standard deviations
    of two lognormal processes (ror1, std1, ror2, std2),
    return a third ror/sigma pair corresponding to the
    moments of the multiplication of the two original processes.
    """
    mean1 = 1 + ror1
    mean2 = 1 + ror2

    mu1, sigma1 = lognormal_moments_to_normal(mean1, std1)
    mu2, sigma2 = lognormal_moments_to_normal(mean2, std2)

    mu3 = mu1 + mu2
    var3 = sigma1 **2 + sigma2 ** 2

    ror3 = math.exp(mu3 + var3 / 2) - 1
    sigma3 = math.sqrt((math.exp(var3) - 1) * math.exp(2 * mu3 + var3))

    return ror3, sigma3

######
#   Model classes used in the simulation
######

class FinanceModel():
    """
    A class representing the financial system in the simulation.

    Contains parameter values for:
      - the capital gains expectations function
      - the dividend
      - the risky asset expectations

    Contains data structures for tracking ROR and STD over time.
    """
    # Empirical data
    sp500_ror = 0.000628
    sp500_std = 0.011988

    # Simulation parameter
    days_per_quarter = 60

    # Expectation calculation parameters
    p1 = 0.1
    delta_t1 = 30

    a = - math.log(p1) / delta_t1

    p2 = 0.1
    delta_t2 = 60

    b = math.log(p2) / delta_t2

    # Quarterly dividend rate and standard deviation.
    dividend_ror = 0.03
    dividend_std = 0.01

    # Data structures. These will change over time.
    starting_price = 100
    prices = [starting_price]
    ror_list = None

    expected_ror_list = None
    expected_std_list = None

    def add_ror(self, ror):
        self.ror_list.append(ror)
        asset_price = self.prices[-1] * (1 + ror)
        self.prices.append(asset_price)
        return asset_price

    def __init__(self, dividend_ror = None, dividend_std = None):

        if dividend_ror:
            self.dividend_ror = divedend_ror

        if dividend_std:
            self.dividend_std = dividend_std

        self.ror_list = []
        self.expected_ror_list = []
        self.expected_std_list = []

    def calculate_risky_expectations(self):
        """
        Compute the quarterly expectations for the risky asset based on historical return rates.

        In this implementation there are a number of references to:
          - paramters that are out of scope
          - data structures that are out of scope

        These should be bundled together somehow.

        NOTE: This MUTATES the 'expected_ror_list' and so in current design
        has to be called on a schedule... this should be fixed.
        """
        # note use of data store lists for time tracking here -- not ideal
        D_t = sum([math.exp(self.a * (l + 1)) for l in range(len(self.ror_list))])
        S_t = math.exp(self.b * (len(self.prices) - 1)) # because p_0 is included in this list.

        w_0 = S_t
        w_t = [(1 - S_t) * math.exp(self.a * (t+1)) / D_t for t in range(len(self.ror_list))]

        expected_ror = w_0 * self.sp500_ror + sum(
            [w_ror[0] * w_ror[1]
             for w_ror
             in zip(w_t, self.ror_list)])
        self.expected_ror_list.append(expected_ror)

        expected_std = math.sqrt(
            w_0 * pow(self.sp500_std, 2) \
            +  sum([w_ror_er[0] * pow(w_ror_er[1] - expected_ror, 2)
                    for w_ror_er
                    in zip(w_t, self.ror_list)]))
        self.expected_std_list.append(expected_std)

    def rap(self):
        """
        Returns the current risky asset price.
        """
        return self.prices[-1]

    def risky_expectations(self):
        """
        Return quarterly expectations for the risky asset.
        These are the average and standard deviation for the asset
        including both capital gains and dividends.
        """
        # expected capital gains quarterly
        ex_cg_q_ror = ror_quarterly(
            self.expected_ror_list[-1],
            self.days_per_quarter
        )
        ex_cg_q_std = sig_quarterly(
            self.expected_std_list[-1],
            self.days_per_quarter
        )

        # factor in dividend:
        cg_w_div_ror, cg_w_div_std = combine_lognormal_rates(
            ex_cg_q_ror,
            ex_cg_q_std,
            self.dividend_ror,
            self.dividend_std
        )

        market_risky_params = {
            'RiskyAvg': 1 + cg_w_div_ror,
            'RiskyStd': cg_w_div_std
        }

        return market_risky_params

    def reset(self):
        """
        Reset the data stores back to original value.
        """
        self.prices = [100]
        self.ror_list = []

        self.expected_ror_list = []
        self.expected_std_list = []



######
#   PNL Interface methods
######

class MarketPNL():
    """
    A wrapper around the Market PNL model with methods for getting
    data from recent runs.

    Parameters
    ----------
    config_file
    config_local_file

    """
    # Properties of the PNL market model
    netlogo_ror = -0.00052125
    netlogo_std =  0.0068

    simulation_price_scale = 0.25
    default_sim_price = 400

    # Empirical data -- redundant with FinanceModel!
    sp500_ror = 0.000628
    sp500_std = 0.011988

    # Storing the last market arguments used for easy access to most
    # recent data
    last_buy_sell = None
    last_seed = None

    # config object for PNL
    config = None

    def __init__(
        self,
        config_file = "../PNL/macroliquidity.ini",
        config_local_file = "../PNL/macroliquidity_local.ini"
    ):
        self.config = UTIL.read_config(
            config_file = config_file,
            config_local_file = config_local_file
        )

    def run_market(self, seed = 0, buy_sell = 0):
        """
        Runs the NetLogo market simulation with a given
        configuration (config), a tuple with the quantities
        for the brokers to buy/sell (buy_sell), and
        optionally a random seed (seed)
        """
        self.last_seed = seed
        self.last_buy_sell = buy_sell

        pnl.run_NLsims(
            self.config,
            broker_buy_limit = buy_sell[0],
            broker_sell_limit = buy_sell[1],
            SEED = seed,
            use_cache = True
        )

    def get_transactions(self, seed = 0, buy_sell = (0,0)):
        """
        Given a random seed (seed)
        and a tuple of buy/sell (buy_sell), look up the transactions
        from the associated output file and return it as a pandas DataFrame.
        """
        logfile = pnl.transaction_file_name(
            self.config,
            seed,
            buy_sell[0],
            buy_sell[1]
        )

        # use run_market() first to create logs
        transactions = pd.read_csv(
            logfile,
            delimiter='\t'
        )
        return transactions

    def get_simulation_price(self, seed = 0, buy_sell = (0,0)):
        """
        Get the price from the simulation run.
        Returns None if the transaction file was empty for some reason.

        TODO: Better docstring
        """

        transactions = self.get_transactions(seed=seed, buy_sell = buy_sell)
        prices = transactions['TrdPrice']

        if len(prices.index) == 0:
            ## BUG FIX HACK
            print("ERROR in PNL script: zero transactions. Reporting no change")
            return None

        return prices[prices.index.values[-1]]


    def daily_rate_of_return(self, seed = None, buy_sell = None):
        ## TODO: Cleanup. Use "last" parameter in just one place.
        if seed is None:
            seed = self.last_seed

        if buy_sell is None:
            buy_sell = self.last_buy_sell

        last_sim_price = self.get_simulation_price(
            seed=seed, buy_sell = buy_sell
        )

        if last_sim_price is None:
            last_sim_price = self.default_sim_price

        ror = (last_sim_price * self.simulation_price_scale - 100) / 100

        # adjust to calibrated NetLogo to S&P500
        ror = self.sp500_std * (ror - self.netlogo_ror) / self.netlogo_std + self.sp500_ror

        return ror


####
#   Broker
####

class Broker():
    """
    A share Broker. Collects orders from agents, then trades on the market
    and reports a rate of return.

    Parameters
    ----------
    market - a MarketPNL instance
    """

    buy_limit = 0
    sell_limit = 0

    buy_sell_history = None

    market = None

    def __init__(self, market):
        self.market = market
        self.buy_sell_history = []

    def transact(self, delta_shares):
        """
        Input: an array of share deltas. positive for buy, negative for sell.
        """
        self.buy_limit += delta_shares[delta_shares > 0].sum()
        self.sell_limit += -delta_shares[delta_shares < 0].sum()

    def trade(self, seed = None):
        """
        Broker executes the trade on the financial market and then updates
        their record of the current asset price.

        Input: (optional) random seed for the simulation
        Output: Rate of return of the asset value that day.
        """

        # use integral shares here.
        buy_sell = (int(self.buy_limit), int(self.sell_limit))
        self.buy_sell_history.append(buy_sell)
        print("Buy/Sell Limit: " + str(buy_sell))

        self.market.run_market(buy_sell = buy_sell, seed = seed)

        # clear the local limits
        self.buy_limit = 0
        self.sell_limit = 0

        return buy_sell, self.market.daily_rate_of_return()
