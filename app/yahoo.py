import platform
import yfinance as yf
import datetime

def yfintut(tickersymbol):
	tickerdata = yf.Ticker(tickersymbol)
	tickerinfo = tickerdata.info
	investment = tickerinfo['shortName']
	print("Investment: "+ investment)

	today = datetime.datetime.today().isoformat()
	print ('Today = ' + today)


	#historical data
	tickerDF = tickerdata.history(period='d1',start='2020-6-1',end=today[:10])
	pricelast = tickerDF['Close'].iloc[-1]
	priceYest = tickerDF['Close'].iloc[-2]

	priceOpen = tickerDF['Open'].iloc[-1]
	priceHigh = tickerDF['High'].iloc[-1]
	priceLow = tickerDF['Low'].iloc[-1]


	change = pricelast - priceYest

	print(investment + ' price last = ' + str(pricelast))
	print('Price Change = ' + str(change))
	print(tickerDF)
	temp = list()
	temp.append(investment)
	temp.append(priceOpen)
	temp.append(priceHigh)
	temp.append(priceLow)
	temp.append(pricelast)
	temp.append(today)



	return temp

# yfintut('TSLA')

#This is how you would put it in a web page
# <!--     <p>{{ myfunc('TSLA') }}</p> -->