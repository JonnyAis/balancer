# note - a better way to do this would be to assemble list of securities from lots and last 30 days of history, cross reference with symbols. pull latest transactions for each in distinct calls


#%% calculate last sale date for each brokerage secutity
#set start and end ranges for transaction call, since defaults to 30 days if not provided
start_range = datetime.now()-timedelta(days = 700)
start_range = start_range.strftime("%m%d%Y")
end_range = datetime.now()+timedelta(days = 1)
end_range = end_range.strftime("%m%d%Y")

###  note - need to figure out how to paginate ###

transactions_resp = session.get(base_url + '/v1/accounts/'+brokerage_id + '/transactions.json',params = {'startDate':start_range,'endDate':end_range, 'sortOrder':'DESC','marker':0,'count':50})
transactions_json = transactions_resp.json()

transactions = pd.DataFrame(transactions_json['TransactionListResponse']['Transaction']) #creates dataframe

#%% converts dates
transactions['transactionDate'] = transactions['transactionDate'].apply(epoch_to_local)
transactions['postDate'] = transactions['postDate'].apply(epoch_to_local)
transactions = transactions.loc[transactions['transactionType'].isin(['Bought','Sold'])] #filters only to bought/sold, i.e., excludes interest, transfers
transactions.set_index('transactionId', inplace = True)

#%% loop through brokerage column to extract symbol and settlement date info
trans_prod_temp = pd.DataFrame()

for i in transactions.index:
    trans_id_temp = i
    sym_temp = transactions.loc[i]['brokerage']['product']['symbol']
    settle_temp = epoch_to_local(transactions.loc[i]['brokerage']['settlementDate'])
    temp_df = pd.DataFrame([[trans_id_temp,sym_temp,settle_temp]], columns = ['transactionId','symbol','settlementDate'])
    trans_prod_temp = trans_prod_temp.append(temp_df)

trans_prod_temp.set_index('transactionId', inplace = True)

transactions = transactions.join(trans_prod_temp)

transactions['transactionDate'] = pd.to_datetime(transactions['transactionDate'])

#%% does transaction window include full 30 days in past?
if (datetime.utcnow() - transactions['transactionDate'].min()).days > 30:
    print('OK - transaction list has full 30 days of history')
else:
    print('Warning - transaction list does NOT have full 30 days of history')

transactions['time_since_trans'] = datetime.utcnow() - transactions['transactionDate']

last_trans_table = transactions.groupby(['symbol','transactionType'])[['transactionDate']].max()
last_trans_table['time_since_last_trans'] = datetime.utcnow() - last_trans_table['transactionDate']
last_trans_table['within30days'] = last_trans_table['time_since_last_trans'] < timedelta(days = 31)
last_trans_table.unstack(level=-1)['within30days'].fillna(False)

#%% join to lots and rename
lots = lots.join(last_trans_table.unstack(level=-1)['within30days'].fillna(False), on = 'symbol') # join lots to last_trans_table
lots.rename(columns={'Bought': 'BoughtWithin30D', 'Sold': 'SoldWithin30D'}, inplace=True)

#%% for how much?
lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0) & (lots['BoughtWithin30D'] == False)]['totalGain'].sum()

#%% which lots have potentail losses to harvest?
lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0) & (lots['BoughtWithin30D'] == False)]

#%%
#if not time bound, how many lots could be sold at a loss?
lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0)]
#%% for how much?
lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0)]['totalGain'].sum()

#%%
account_balance.loc[account_balance['accountName']=='Brokerage']

#for each security, how long to wait and how much to save?

#%% run and execute

if len(lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0) & (lots['BoughtWithin30D'] == False)].index) > 0:
    print('TLH') # open TLH order file
    exec(open("orders_live_tlh.py").read())

# %%
