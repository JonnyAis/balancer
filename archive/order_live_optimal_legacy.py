#%% final prep of optimal frame
optimal.loc[optimal['buy_amt'] > 0, 'action'] = 'BUY'
optimal.loc[optimal['buy_amt'] < 0, 'action'] = 'SELL'

optimal = optimal.merge(account_balance[['accountIdKey','asset_class','preferred_etf']].drop_duplicates())
optimal = optimal.sort_values(by = ['action'], ascending=False) #sell before buy

#%% execute
order_confirms = []

for row in optimal.itertuples():
   print(row)
   order_symbol = row.preferred_etf
   order_action = row.action
   order_quantity = int(abs(row.buy_amt))
   order_acct_key = row.accountIdKey
   client_order_id = ''.join(random.choice(string.ascii_lowercase) for i in range(20)) #any unique string up to 20 characters

   payload_preview_temp = {  
      "PreviewOrderRequest":{  
         "orderType":"EQ",
         "clientOrderId": client_order_id,
         "Order":[  
            {  
               "allOrNone":"false",
               "priceType":"MARKET",
               "orderTerm":"GOOD_FOR_DAY",
               "marketSession":"REGULAR",
               "stopPrice":"",
               "limitPrice":"",
               "Instrument":[  
                  {  
                     "Product":{  
                        "securityType":"EQ",
                        "symbol":order_symbol
                     },
                     "orderAction":order_action,
                     "quantityType":"QUANTITY",
                     "quantity":order_quantity
                  }
               ]
            }
         ]
      }
   }

   payload_preview = json.dumps(payload_preview_temp)
   headers = {'Content-Type': 'application/json'} 

   # send and process order preview payload
   preview_resp = session.post('https://api.etrade.com/v1/accounts/'+order_acct_key+'/orders/preview.json', data = payload_preview, headers = headers, header_auth = True)
   preview_json = preview_resp.json()
   
   if preview_resp.status_code == 200:
      preview_id = preview_json['PreviewOrderResponse']['PreviewIds'][0]['previewId']

      # place order - build payload

      payload_place_temp = {  
         "PlaceOrderRequest":{  
            "orderType":"EQ",
            "clientOrderId":client_order_id,
            "PreviewIds":[  
               {  
                  "previewId":preview_id
               }
            ],
            "Order":[  
               {  
                  "allOrNone":"false",
                  "priceType":"MARKET",
                  "orderTerm":"GOOD_FOR_DAY",
                  "marketSession":"REGULAR",
                  "stopPrice":"",
                  "limitPrice":"",
                  "Instrument":[  
                     {  
                        "Product":{  
                           "securityType":"EQ",
                           "symbol":order_symbol
                        },
                        "orderAction":order_action,
                        "quantityType":"QUANTITY",
                        "quantity":order_quantity
                     }
                  ]
               }
            ]
         }
      }

      payload_place = json.dumps(payload_place_temp)

      # place order - send and process
      order_resp = session.post('https://api.etrade.com/v1/accounts/'+ order_acct_key+'/orders/place.json', data = payload_place, headers = headers, header_auth = True)
      order_confirms.append(order_resp.json()['PlaceOrderResponse'])

time.sleep(60*15)
exec(open("etrade_live.py").read())