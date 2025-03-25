# Importing Packages
import matplotlib.pyplot as plt
import random
import pandas as pd
import numpy as np
import yfinance as yf


## define distribution of outcomes
stock_mu = 0.02
stock_sigma = 0.04
bond_mu = 0.005
bond_sigma = 0.01


return_stock = random.gauss(mu=stock_mu, sigma=stock_sigma)
return_bond = random.gauss(mu=bond_mu, sigma=bond_sigma)
    

### set up porfolio

# starting value
portfolio_starting_value = 1000
# balance between asset classes
portfolio_balance_df = pd.DataFrame(data = [[0.50,0.50]], columns = ['stock_balance','bond_balance'])

df = pd.DataFrame(
    columns = ['period','total_start_value',
    'start_value_stock','start_value_bond',
    'stock_gain_percent','stock_gain_value',
    'bond_gain_percent','bond_gain_value',
    'end_value_stock', 'end_value_bond',
    'total_gain','total_end_value']).set_index('period')


def market_returns(period, stock_value, bond_value):
    return_stock = random.gauss(mu=stock_mu, sigma=stock_sigma)
    return_bond = random.gauss(mu=bond_mu, sigma=bond_sigma)

    dict = {'period': period, 
    'total_start_value': stock_value + bond_value,
    'start_value_stock': stock_value,
    'start_value_bond': bond_value,
    'stock_gain_percent': return_stock,
    'stock_gain_value': stock_value*return_stock,
    'bond_gain_percent': return_bond,
    'bond_gain_value': bond_value * return_bond,
    'end_value_stock': stock_value*(1+return_stock),
    'end_value_bond': bond_value*(1+return_bond),
    'total_gain': stock_value*return_stock + bond_value * return_bond,
    'total_end_value': stock_value + bond_value + stock_value*return_stock + bond_value * return_bond}

    return pd.DataFrame([dict]).set_index('period')  

# prove to yourself it works - specific values 
market_returns(1, 800, 200)

# prove to yourself it works - abstract
market_returns(1, portfolio_starting_value*portfolio_balance_df['stock_balance'][0],portfolio_starting_value*portfolio_balance_df['bond_balance'][0])

for i in range(1,10*252):
    if df.empty:
        df = market_returns(i, portfolio_starting_value*portfolio_balance_df['stock_balance'][0], portfolio_starting_value*portfolio_balance_df['bond_balance'][0])
    else:
        df = pd.concat([
                df,
                market_returns(i, df.loc[i-1].end_value_stock, df.loc[i-1].end_value_bond)
                ])

display(df)
#what percentage of assets are in stocks by the end?
df.loc[len(df-1)].end_value_stock / df.loc[len(df-1)].total_end_value 

#over 99% stock. and we're super rich!


### now get the actual distribution
range_start = "2011-12-28"
range_end = "2023-12-28"

v500_data = yf.download("VOO", start = range_start, end = range_end, interval="1d")
mub_data = yf.download("MUB", start = range_start, end = range_end, interval="1d")
v500_returns = v500_data['Adj Close'].pct_change().dropna() 
mub_returns = mub_data['Adj Close'].pct_change().dropna()


mean_vector = v500_returns.mean(),mub_returns.mean()
covariance_matrix = np.cov(v500_returns,mub_returns)

return_stock, return_bond = np.random.multivariate_normal(mean=mean_vector, cov=covariance_matrix)


def market_returns(period, stock_value, bond_value):
    return_stock, return_bond = np.random.multivariate_normal(mean=mean_vector, cov=covariance_matrix)

    dict = {'period': period, 
    'total_start_value': stock_value + bond_value,
    'start_value_stock': stock_value,
    'start_value_bond': bond_value,
    'stock_gain_percent': return_stock,
    'stock_gain_value': stock_value*return_stock,
    'bond_gain_percent': return_bond,
    'bond_gain_value': bond_value * return_bond,
    'end_value_stock': stock_value*(1+return_stock),
    'end_value_bond': bond_value*(1+return_bond),
    'total_gain': stock_value*return_stock + bond_value * return_bond,
    'total_end_value': stock_value + bond_value + stock_value*return_stock + bond_value * return_bond}

    return pd.DataFrame([dict]).set_index('period')

# plot result
plt.plot(
    df.index, 
    df.end_value_stock, color='blue', label='VOO')

plt.plot(
    df.index, 
    df.end_value_bond, color='red', label='MUB')

plt.plot(
    df.index, 
    df.total_end_value, color='black', label='Total')

df.drop(df.index)

#run it!
for i in range(1,10*252):
    if df.empty:
        df = market_returns(i, portfolio_starting_value*portfolio_balance_df['stock_balance'][0], portfolio_starting_value*portfolio_balance_df['bond_balance'][0])
    else:
        df = pd.concat([
                df,
                market_returns(i, df.loc[i-1].end_value_stock, df.loc[i-1].end_value_bond)
                ])

display(df)




# ok now let's run it 100 times
def simulate_portfolio(n): 
    results = [] 
    for i in range(n): 
        df = market_returns(1, 800, 200) 
        for j in range(1, 10*252):
            df = pd.concat([df, market_returns(j+1, df.loc[j].end_value_stock, df.loc[j].end_value_bond)]) 
        results.append(df['total_end_value'].iloc[-1]) 
    return pd.DataFrame(results, columns=['final_value'])

sim_results = simulate_portfolio(30)
sim_results.final_value.mean()
sim_results.final_value.std()

sim_results.final_value.plot.hist(bins = 15)
plt.axvline(sim_results.final_value.mean(), color = 'red',linestyle='dashed')
plt.show()
plt.savefig("80_20_total_return_distributions.png")


### store results of 80/20 split so we can compare with a different allocation
#plt.show()


#manually store for now
sim_results_80_20 = sim_results
sim_results_90_10
sim_results_70_30
sim_results_50_50



#plots of distributions of VOO and MUB

v500_data['Adj Close'].plot()
plt.xlabel("Date")
plt.ylabel("Adjusted")
plt.title("VOO Price data")
plt.show()

voo_daily_returns = voo_hist['Adj Close'].pct_change()

fig = plt.figure()
ax1 = fig.add_axes([0.1,0.1,0.8,0.8])
ax1.plot(v500_returns)
ax1.set_xlabel("Date")
ax1.set_ylabel("Percent")
ax1.set_title("VOO daily returns data")
plt.show()


fig = plt.figure()
ax1 = fig.add_axes([0.1,0.1,0.8,0.8])
v500_returns.plot.hist(bins = 60)
ax1.set_xlabel("Daily returns %")
ax1.set_ylabel("Percent")
ax1.set_title("VOO daily returns data")
ax1.text(-0.35,200,"Extreme Low\nreturns")
ax1.text(0.25,200,"Extreme High\nreturns")
plt.show()





#actual mu and sigma for VOO and MUB


#### note - don't throw out the rebalance part
def market_returns(portfolio_current_total_value):
    begin_values = portfolio_current_total_value * portfolio_balance_df ##here <------
    total_begin_value = portfolio_current_total_value   
    return_stock = random.gauss(mu=0.1, sigma=0.05)
    return_bond = random.gauss(mu=0.05, sigma=0.01)
    daily_gain = begin_values['stock_balance']*(return_stock) + begin_values['bond_balance']*(return_bond)
    print('daily gain: ' + str(daily_gain))
    ending_value = total_begin_value + daily_gain
#    portfolio_current_total_value = total_begin_value + daily_gain
    print('ending balance: ' + str(ending_value))
