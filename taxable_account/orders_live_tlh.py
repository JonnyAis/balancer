#%%
tlh_lots = lots.loc[(lots['accountIdKey']==brokerage_id) & (lots['totalGain'] < 0) & (lots['BoughtWithin30D'] == False)]

tlh_lots['totalGain'].sum()

tlh_lots['remainingQty']

#%% execute
for row in tlh_lots.itertuples():
   print(row)
   order_symbol = row.symbol
   order_action = 'SELL'
   order_quantity = row.remainingQty
   order_acct_key = brokerage_id
   lot_id = row.Index
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
                     "quantity":order_quantity,
                     "Lots":{
                              
                                 "Lot":[{
                                    "id":lot_id,
                                    "size":order_quantity
                                       }]
                              
                           }  
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
                        "quantity":order_quantity,
                        "Lots":{
                              
                                 "Lot":[{
                                    "id":lot_id,
                                    "size":order_quantity
                                       }]
                              
                                }
                     }
                  ]
               }
            ]
         }
      }

      payload_place = json.dumps(payload_place_temp)


      # place order - send and process
      order_resp = session.post('https://api.etrade.com/v1/accounts/'+ order_acct_key+'/orders/place.json', data = payload_place, headers = headers, header_auth = True)
      order_resp
      order_resp.content

# %%
