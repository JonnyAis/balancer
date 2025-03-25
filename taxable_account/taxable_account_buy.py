### which securities to buy for each class?

#%% filters out securities sold within the last 30 days
taxable_buy_choices = last_trans_table.reset_index().loc[ \
    ~((last_trans_table.reset_index()['transactionType']=='Sold') \
    & (last_trans_table.reset_index()['within30days']))]

#%%
taxable_buy_choices

#%%
account_balance[account_balance['accountName']=='Brokerage'][['asset_class','target_percent','buy/sell','marketValue','target_value']]



#%% how much loss can be harvested from each security?
lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0)].groupby('symbol')['totalGain'].sum()

symbols.join(taxable_buy_choices['symbol']).shape

taxable_buy_choices.join(symbols['asset_class'])

taxable_buy_choices.shape


type(taxable_buy_choices['symbol'])

list(taxable_buy_choices['symbol'])

symbols.loc[list(taxable_buy_choices['symbol'])]

np.unique(lots['symbol'].values)

symbols.loc[isin(['IJR','ITOT'])]

### how much to buy of each class

### optimal shares among two constraints above
