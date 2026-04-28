
from ib_insync import IB, Option
import time

def test():
    ib = IB()
    try:
        ib.connect('127.0.0.1', 7496, clientId=99)
        # AMD short leg from user's logs
        contract = Option(conId=875302378, symbol='AMD', lastTradeDateOrContractMonth='20260515', strike=335.0, right='C', exchange='SMART')
        ib.qualifyContracts(contract)
        print(f"Contract qualified: {contract}")
        
        print("Requesting snapshot...")
        ticker = ib.reqMktData(contract, "", snapshot=True)
        for _ in range(10):
            ib.sleep(0.5)
            print(f"  Bid: {ticker.bid}, Ask: {ticker.ask}, Last: {ticker.last}, Close: {ticker.close}")
            if ticker.bid > 0 or ticker.last > 0:
                break
        
        ib.disconnect()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
