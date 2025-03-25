# %% assign base url
base_url = session.service.base_url

#%% gets account list
resp_acct_list = session.get('v1/accounts/list.json')
accounts_json = resp_acct_list.json()
accounts = pd.DataFrame(accounts_json['AccountListResponse']['Accounts'].get('Account')).set_index('accountId')
del(accounts_json,resp_acct_list)
accounts

#%% brokerage accountIdKey
brokerage_id = accounts[accounts['accountName']=='Brokerage']['accountIdKey'][0]

#%% loops through account IDs to get all positions

positions_list = []
cash_list = []

for key in accounts.accountIdKey:
    port_resp = session.get(base_url + '/v1/accounts/' + key + '/portfolio.json', params = {'totalsRequired':True})
    port_temp_json = port_resp.json()
    port_temp = pd.DataFrame(port_temp_json['PortfolioResponse']['AccountPortfolio'])

    pos_temp = pd.DataFrame(port_temp['Position'][0]).set_index('positionId')
    acct_id_temp = port_temp['accountId'][0]
    pos_temp['accountId'] = acct_id_temp

    positions_list.append(pos_temp)

    #creates table to store cash balance
    cash_temp = port_temp_json['PortfolioResponse']['Totals']['cashBalance']
    cash_temp_df = pd.DataFrame([[acct_id_temp, cash_temp]], columns = ['accountId','cash']).set_index('accountId')
    cash_list.append(cash_temp_df)

    # deletes temp dataframes
    del(port_resp)
    del(port_temp_json)
    del(port_temp)
    del(acct_id_temp)
    del(pos_temp)
    del(cash_temp, cash_temp_df)

positions = pd.concat(positions_list)
cash = pd.concat(cash_list)


del(key)
positions.rename(columns={'symbolDescription':'symbol'},inplace = True) #renames symbol column
positions
#%% creates symbols dataframe, gets prices for each symbol in portfolio

size = positions.Quick.values.size
symbols_list = []

for i in range(size):
    symbols_temp = positions.Quick.values.item(i)
    symbols_temp['symbol'] = positions.Product.values.item(i)['symbol']    
    symbols_list.append(symbols_temp)

symbols = pd.DataFrame(symbols_list)
symbols = symbols[['symbol']].drop_duplicates().set_index('symbol')

del(symbols_temp, i, size)

symbols

#%% get prices for symbols in portfolio
quote_url = 'https://api.etrade.com/v1/market/quote/' #base url for quote API call
sym_string = ','.join(list(symbols.index)) #generates comma sep string from symbol list
sym_size = len(symbols)
quote_resp = session.get(quote_url + sym_string + '.json',params = {'detailFlag':'FUNDAMENTAL'})
quote_json = quote_resp.json()

quote_list = []

for q in list(range(0,sym_size)):
    q_fun = quote_json['QuoteResponse']['QuoteData'][q]['Fundamental']['lastTrade']
    q_sym = quote_json['QuoteResponse']['QuoteData'][q]['Product']['symbol']
    quote_list.append({'symbol': q_sym, 'lastTrade': q_fun})

quote_temp = pd.DataFrame(quote_list).set_index('symbol')

symbols = symbols.join(quote_temp)

del(q, q_fun,q_sym, quote_temp, quote_json, quote_resp, quote_url, sym_size, sym_string)

symbols

#%% maps tickers to asset classes

# class of symbols
symbols_classes = [['BND','Bonds'],\
        ['JMST','Bonds'],\
        ['SHM','Bonds'],\
        ['LDEM','International'],\
        ['IXUS','International'],\
        ['ESGE','International'],\
        ['SPDW','International'],\
        ['VBR','Small'],\
        ['VOO','Large'],\
        ['VWO',	'International'],\
        ['MUB','Bonds'],\
        ['ITOT','Large'],\
        ['VEU','International'],\
        ['VO','Small'],\
        ['VTEB','Bonds'],\
        ['SPY','Large'],\
        ['IJR','Small'],\
        ['SUB','Bonds'],\
        ['TAXF','Bonds']]

mapping = pd.DataFrame(symbols_classes,columns = ['symbol','asset_class']).set_index('symbol')

symbols = symbols.merge(mapping, on = 'symbol')
del symbols_classes
del mapping
symbols


#%% define optimal balance
classes = [['Large',40,'VOO'],\
    ['Small',12,'VBR'],\
    ['International',28,'IXUS'],\
    ['Bonds',20,'BND'],\
    ['Cash',0]]

class_balance = pd.DataFrame(classes,columns = ['asset_class','target_percent','preferred_etf'])
del classes
class_balance = class_balance.set_index('asset_class')
class_balance

#%% loop through lotsDetails to get all lot info

lots_list = []

for lot_url in list(positions['lotsDetails']):
    lot_resp = session.get(lot_url)
    lot_resp_json = lot_resp.json()
    lots_temp = pd.DataFrame(lot_resp_json['PositionLotsResponse']['PositionLot'])
    lots_list.append(lots_temp)

lots = pd.concat(lots_list, ignore_index=True)

lots
#%% cleans up lots dataframe

lots = lots.set_index('positionLotId') #sets index to unique identifier for lot

def epoch_to_local(epoch): # converts epoch to local time
    return time.ctime((epoch/1000)+10800) #note the extra 10800 is to adjust for the 3 hour difference (60*60*3 = 10800)

lots['acquiredDateLocal'] = lots['acquiredDate'].apply(epoch_to_local)

lots = lots.join(positions[['symbol','accountId']], on = 'positionId')

lots = lots.join(accounts['accountIdKey'], on = 'accountId') #pull in account key

lots

#%% join last trade price and asset class into positions and lots
lots = lots.join(symbols, on = 'symbol')
positions = positions.join(symbols, on = 'symbol')


#%% creates account balance table with all combinations of accounts and asset classes 
class_list = list(class_balance.index)
account_list = list(accounts.index)
account_balance = pd.DataFrame(itertools.product(account_list, class_list), columns = ['accountId','asset_class'])
account_balance = account_balance.merge(accounts[['accountName','accountIdKey']], on = 'accountId').merge(class_balance, on = 'asset_class')

del(class_list, account_list,class_balance)



#%% replaces BND with MUB for taxable account
account_balance.loc[(account_balance['accountName'] == 'Brokerage') & (account_balance['asset_class'] =='Bonds'),'preferred_etf'] = 'MUB'

#%% sums account balance by class, pulls into account balance dataframe
account_balance_by_class = lots.groupby(['accountId','asset_class']).sum(numeric_only=True)[['marketValue']]

account_balance = account_balance.merge(account_balance_by_class, on = ['accountId','asset_class'])
del(account_balance_by_class)


#%% calculates total current by account and asset class, merges with account_balance_by_class
total_balance_by_acct = lots.groupby('accountId').sum(numeric_only=True)[['marketValue']] 

total_balance_by_acct = total_balance_by_acct.join(cash)
total_balance_by_acct['total_current_acct_value'] = total_balance_by_acct['marketValue']+total_balance_by_acct['cash']

account_balance = account_balance.merge(total_balance_by_acct['total_current_acct_value'], on = 'accountId')
total_balance_by_acct = total_balance_by_acct.join(accounts['accountIdKey']) #pull in account key
del(cash)

#%% calculates target amount by account and asset class ---- name sure names are right
account_balance['target_value'] = account_balance['total_current_acct_value']*account_balance['target_percent']/100
account_balance = account_balance.merge(symbols['lastTrade'], left_on = 'preferred_etf', right_index = True, how = 'left')
account_balance['buy/sell'] = account_balance['target_value'] - account_balance['marketValue']


#%% creates frame of current shares by account, class (i.e., ticker)
curr_shares = pd.DataFrame(lots.groupby(['accountIdKey','asset_class'])['remainingQty'].sum()).unstack()['remainingQty'][['Large', 'Small', 'International', 'Bonds']]
curr_shares = curr_shares[curr_shares.index != brokerage_id] #drop brokerage
curr_shares = curr_shares.reset_index().rename_axis(None, axis=1).set_index('accountIdKey')

#%% defines up_down function to create possible allocations to test based on current values

perc_diff = 0.05 #sets allowable difference from current 

def up_down(new_result_x, perc_diff): #could later make it calculate length of the array and loop through rather than prespecify
    if isinstance(new_result_x, float):
        a = list(range(math.floor(new_result_x*(1-perc_diff)),math.ceil(new_result_x*(1+perc_diff))))
        return pd.DataFrame(a, columns=['Value'])
    else:
        ranges = [list(range(math.floor(x * (1 - perc_diff)), math.ceil(x * (1 + perc_diff)))) for x in new_result_x]
        return pd.DataFrame(itertools.product(*ranges), columns=['Large', 'Small', 'International', 'Bonds']).drop_duplicates()

#%% creates results to test dataframe
results_to_test = []

for index, row in curr_shares.iterrows():
    df_temp = pd.DataFrame(up_down(row.to_numpy(),perc_diff))
    df_temp['accountIdKey'] = index
    results_to_test.append(df_temp)

results_to_test = pd.concat(results_to_test, ignore_index=True)

del(df_temp, index, row)

results_to_test.reset_index(drop = True, inplace = True)
results_to_test = results_to_test.merge(total_balance_by_acct[['total_current_acct_value','accountIdKey']], how = 'left', left_on = 'accountIdKey', right_on = 'accountIdKey')

#%% pulls in data from account_balance
results_to_test = results_to_test.merge(account_balance.pivot(index='accountIdKey',columns = 'asset_class',values='target_value').reset_index().rename_axis(None, axis=1).set_index('accountIdKey'),how='left',left_on = 'accountIdKey', right_index = True,suffixes=('_shares','_target_value'))

#%%
def imbalance(Large_shares, Small_shares, International_shares, Bonds_shares,
            target_large, target_small, target_international, target_bonds,
            total_current_acct_value):

    #calculates new amounts based on function inputs
    new_large = Large_shares * symbols.loc['VOO','lastTrade']
    new_small = Small_shares * symbols.loc['VBR','lastTrade']
    new_international = International_shares * symbols.loc['IXUS','lastTrade']
    new_bonds = Bonds_shares * symbols.loc['BND','lastTrade']
    new_cash = total_current_acct_value - new_large - new_small - new_international - new_bonds

    #calculates total imbalance
    #imbalance_amt = new_cash + abs(target_large - new_large) + abs(target_small - new_small) + abs(target_international - new_international) + abs(target_bonds - new_bonds)
    imbalance_amt = new_cash**3 + (target_large - new_large)**2 + (target_small - new_small)**2 + (target_international - new_international)**2 + (target_bonds - new_bonds)**2
    return (imbalance_amt, new_cash)


#%% runs imblanace to calculate imbalance_amt and new cash
results_to_test['imbalance'],results_to_test['new_cash'] = imbalance(Large_shares = results_to_test['Large_shares'],
                                Small_shares = results_to_test['Small_shares'],
                                International_shares = results_to_test['International_shares'],
                                Bonds_shares = results_to_test['Bonds_shares'],
                                target_large = results_to_test['Large_target_value'],
                                target_small = results_to_test['Small_target_value'],
                                target_international = results_to_test['International_target_value'],
                                target_bonds = results_to_test['Bonds_target_value'],
                                total_current_acct_value = results_to_test['total_current_acct_value'])


#%% generates index table of min values

results_to_test = results_to_test[results_to_test['new_cash']>=0] #only consider possible choices

min_indexes = []

for key in results_to_test['accountIdKey'].drop_duplicates():
    min_indexes.append(results_to_test[results_to_test['accountIdKey']==key]['imbalance'].astype(float).idxmin())

del(key)


#%% transform optimal, pulls in current share amounts and calculates num of shares to buy/sell

optimal_pivoted = results_to_test.loc[min_indexes][['accountIdKey','Large_shares','Small_shares','International_shares','Bonds_shares']]
optimal = pd.melt(optimal_pivoted,id_vars=['accountIdKey'],value_vars=['Large_shares','Small_shares','International_shares','Bonds_shares'])
optimal['asset_class'] = optimal.variable.str.replace('_shares','')

curr_shares_melted = pd.melt(curr_shares.reset_index(),id_vars=['accountIdKey'],value_vars=['Large','Small','International','Bonds'])

optimal = optimal.merge(curr_shares_melted, left_on =['accountIdKey','asset_class'],right_on = ['accountIdKey','variable'], suffixes = ('_new','_curr'))[['accountIdKey','asset_class','value_new','value_curr']]
optimal['buy_amt'] = optimal['value_new']-optimal['value_curr']
optimal = optimal[optimal['buy_amt'] !=0] #filter out those with no action

results_to_test.loc[min_indexes].sort_values('imbalance')

#%% wait and retry if optimal empty
if optimal.empty:
    time.sleep(60*30)
    exec(open("etrade_live.py").read())

#%% run order script
if nyse.open_at_time(schedule, datetime.now(timezone('America/New_York'))): #is NYSE open now?
    exec(open("order_live_optimal.py").read())
else:
    time.sleep(60*30)
    exec(open("etrade_live.py").read())