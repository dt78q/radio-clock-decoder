'''Decoder for the output of a radio module receiving the long-wavelength
PWM date-time code from radio station JJY
JJY broadcasts JST; Japan has no DST
https://www.nict.go.jp/en/sts/jjy.html
The time stamp refers to the beginning of the 60 sec period
The RTC will be set to UTC; use loc_time() for geographic local time with daylight saving times
'''
# def RCget():
########## settings ##############
GPIOsig = 1  # input (GP#) for the radio module
input_pull = 'off'  # 'off', 'up' or 'down'  # the input may or may not need a pull up
enable = 'off'  # 'off', 'high' or 'low'  # for if module has an enable/standby control
GPIOen = 2  # output for the enable connection if used

timeout = 15  # minutes

'''For sampling the timecode signal, need delta_t - the offset of the 
millisecond counter (at ...000 ms) from the start of the timecode marker pulse
the millisecond counter is independent of the RTC; setting the RTC will not change delta_t
'''
# set known delta_t ('delay') for testing, otherwise must be set to 'None'
delta_t = None

'''After sampling, two pairs of indices set the pulse parameters in 10 ms steps
they might differ significantly from the specification so tweak these
to get clean pulses using show_smpls. Should be all ones or all zeros
but might vary from time to time so set conservatively (smaller range)
specification is bitB: 200 - 500 ms, bitA: 500 - 800 ms
bitA is the timecode data, bitB is the marker
'''
show_smpls = False  # use to check for clean pulses
A1, A2 = 50, 80  # bit A, 500-800 ms
B1, B2 = 20, 50  # bit B, 200-500 ms

######### end of settings #######

from machine import Pin, RTC, Timer # type: ignore
from time import time, ticks_ms, ticks_diff, gmtime, mktime, sleep_ms # type: ignore
from array import array
rtc=RTC()

# trig = Pin(4, Pin.OUT)  # to trigger an oscilloscope to monitor the pulses

if input_pull == 'up':
    rc_signal = Pin(GPIOsig, Pin.IN, Pin.PULL_UP)
elif  input_pull == 'down':
    rc_signal = Pin(GPIOsig, Pin.IN, Pin.PULL_DOWN)
else:
    rc_signal = Pin(GPIOsig, Pin.IN)

if enable != 'off':
    rc_enable = Pin(GPIOen, Pin.OUT)  # receiver module enable
    rc_enable.value(0) if enable=='high' else rc_enable.value(1)

# Set interrupt to detect the leading edge of the pulses
def isr_trig(Pin):
    delta[0] = ticks_ms()

# Set interrupt to sample the pulses
def isr_sample(timer):
    samples[0] +=1
    samples[samples[0]] = rc_signal.value()

# Some stats
def mean(data): return sum(data) / len(data)
def mid(data): return (max(data) + min(data))/2
def st_dev(data): return (sum([(x - mean(data))**2 for x in data]) /(len(data)-1))**0.5

# Delete outlier
def del_outlr(data):
    outlier = max(data) if mid(data) > mean(data) else min(data)
    data.remove(outlier)
    # print("removed:", outlier)

# Measure the time offset of the pulses from the RTC 'tick' over period = secs
def get_deltas(secs):
    start = ticks_ms()
    count = secs * 2
    while count > 0  and ticks_diff(ticks_ms(), start)/1000 < secs-1:
        # poll for pulse detected
        if delta[0] != -1:
            count -=1
            deltas.append(delta[0]%1000)
            shifts.append((delta[0]+500)%1000)
            delta[0] = -1
            print(deltas[-8:])
            sleep_ms(50)
    return deltas, shifts

################### Start ################################
print('>>>> rc_jjy >>>>')
rc_sync = False
if enable != 'off':
    sleep_ms(50)  # to toggle rc_enable
    rc_enable.value(1) if enable=='high' else rc_enable.value(0)
abort = False

################### Get "delta_t" ########################
if delta_t:
    print("Using set value of delta_t =",delta_t,"ms for testing")
else:
    # Measure delta_t (offset of timecode pulses from millisecond counter ..000 ms)
    rc_signal.irq(trigger=Pin.IRQ_FALLING, handler=isr_trig)
    delta = array('i',[-1])  # array avoids both memory allocation and global in ISR (???)
    elapsed = 0
    while True:
        elapsed +=1
        if elapsed == 10:
            print("Waiting for timecode pulses")
        if elapsed == 20:
            print("might take a minute or two if just powered up")
        if delta[0] !=-1:  # pulse received
            delta[0] = -1
            sleep_ms(1000)
            break
        sleep_ms(1000)  # allow agc etc. to settle

    print("Measuring the RTC offset /ms from the timecode pulse")
    '''delta_t is average of (cleaned up) delta values
    occasionally delta_t will be so close to zero (or 999) that some delta%1000 values wrap around
    to prevent this 'shifts' is deltas shifted by 500 ms then we check if values are more consistent
    (shift may also be triggered by extraneous noise pulses)
    In each set of delta_t there will be a normal distribution of values due to jitter but there may 
    also be random noise pulses. In a small dataset outliers can produce a large error in the average
    so are removed
    '''
    deltas, shifts = [],[]
    shift = 0
    period = 16  # seconds
    deltas, shifts = get_deltas(period)
    if len(deltas) == period * 2:
        print("Noise too high, aborting")
        abort = True
    elif len(deltas) > period * 1.25:
        print("poor signal, another 48 secs...\n")
        period = 64
        deltas, shifts = get_deltas(period - 16)
    rc_signal.irq(handler=None)
    if not abort:
        num_deltas = len(deltas)
        while st_dev(deltas) > 5 and len(deltas) > 8: del_outlr(deltas)
        while st_dev(shifts) > 5 and len(shifts) > 8: del_outlr(shifts)
        if len(deltas) < len(shifts):
            deltas = [x for x in shifts]
            shift = 500
            print('shift = 500 ms')
        if num_deltas - len(deltas) > 0:
            print(num_deltas - len(deltas), "outliers removed")
        deltas.sort()
        vals = 'values = ['
        for x in deltas:
            vals += str(x+shift)+', '
        vals = vals[:-2] +']'
        print(vals)
        delta_t = round((mean(deltas) - shift)%1000)  # final %1000 in case shift makes delta_t < 0
        print(f'delay = {delta_t} ms')

############### Receive timecode ####################
# Know when to expect each timecode pulse now - sample timecode to detect pulse width
if not abort:
    yrs, doys, mods = [],[],[]  # lists for received values
    # display lists to show progress
    dt = "Year:{}\nDoy:{}\nMinute of day:{}"
    # Check if RTC is set to recent time
    if gmtime()[0] > 2021:
        # Pico hasn't cold started - use current rtc time for first set of values
        print('RTC time is', gmtime()[:5],'- using as first set')
        # JJY transmits Japan Standard Time (UTC +9) for the current minute
        year, _, _, hour, minute, _, _, doy = gmtime(time() +9*3600) # type: ignore
        yrs, doys, mods = [year%100], [doy], [hour*60 + minute]
    else:
        print('RTC time is', gmtime()[:5],' - cold start')

    # Indicators of confirmed parameters (year, doy)
    match = [False]*2
    # Create registers for received timecode
    tcA = '                                                            '  # need all 60 seconds
    tcB = '        '  # for the frame markers

    # need about 80 samples at 100 S/s to cover 'marker' (~200-500 ms) & 'signal' (~500-800 ms)
    samples = array('B', (0 for _ in range(82)))  # samples[0] is for indexing
    elapsed = 0  # secs
    while True:
        # poll to trigger sampling at delta_t
        if ticks_ms()%1000 == delta_t:
            # trig.value(1)  # scope trigger
            # Set up timecode sampling
            sample = Timer(period=10, callback=isr_sample)
            while samples[0] < len(samples)-1:  # incremented by the interrupt
                pass  # keep sampling...
            # trig.value(0)  # scope trigger
            Timer.deinit(sample)
            samples[0] = 0
            if show_smpls:
                print(samples[0], samples[A1:A2], samples[B1:B2])

            # Shift left and append new values, samples are averaged to reduce noise
            tcA = tcA[1:] + str(round(mean(samples[A1:A2])))
            tcB = tcB[1:] + str(round(mean(samples[B1:B2])))
            # if inverted pulse is necessary 
            # tcA = tcA[1:] + str(1 - round(mean(samples[A1:A2])))
            # tcB = tcB[1:] + str(1 - round(mean(samples[B1:B2])))
            print(tcB, tcA)
            elapsed +=1  # seconds elapsed (for matching successive minute values)

            # Timecode end marker, actually detects the start of the following minute
            # thus an additional '0' is added to the end of the tc string
            if tcB.endswith('0011'):
                print('\nEnd of timecode')
                # Decode timecode
                # Year value
                # if not already found and if bits exist (no parity bits)
                if not match[0] and tcA[-20] != ' ':
                    yr = int(tcA[-20:-16],2)*10 + int(tcA[-16:-12],2)  # decode BCD to decimal
                    if 23 < yr < 100:  # if value is sensible
                        yrs = yrs[-4:] + [yr]  # append to list, truncate to 5 values
                        if yrs.count(yr) == 3:  # when three values match accept value
                            year = 2000 + yr
                            match[0] = True
                            print("Match year!")
                # Day of year
                if not match[1] and tcA[-39] != ' ':
                    dy = int(tcA[-39:-37],2)*100 + int(tcA[-36:-32],2)*10 + int(tcA[-31:-27],2)
                    if 0 < dy <= 366:
                        doys = doys[-4:] + [dy]
                        if doys.count(dy) == 3:
                            doy = dy
                            match[1] = True
                            print("Match day of year!")
                # Hour & minute (share a parity bit)
                # to allow rollover of hour, hour & minute is summed into minute_of_day
                # and are always determined last
                if (tcA[-60]!=' '
                    and ((tcA[-49:-42] + tcA[-25]).count('1'))%2 == 0  # if even parity checks
                    and ((tcA[-60:-52] + tcA[-24]).count('1'))%2 == 0
                    ):
                    hr = int(tcA[-49:-47],2)*10 + int(tcA[-46:-42],2)
                    mn = int(tcA[-60:-57],2)*10 + int(tcA[-56:-52],2)
                    if hr < 24 and mn < 60:
                        mod = hr*60 + mn
                        # Update mods list to match to current minute
                        mods = [x + elapsed//60 for x in mods]
                        elapsed = 0
                        mods = mods[-4:] + [mod]
                        if mod <5:  # danger of day etc. having rolled over so restart matching
                            yrs = yrs[-1:]
                            doys = doys[-1:]
                            match = [False]*2

                    # If all values match after two full sets of values, good confidence:
                    if (min([len(yrs), len(doys)])>1 and
                        max(len(set(yrs)), len(set(doys))) ==1):
                            year, doy = 2000+yr, dy
                            match = [True, True]

                    # only set clock when current minute is found
                    if all(match) and ((len(mods)>1 and len(set(mods)) == 1) or mods.count(mod) >= 3):
                        print(dt.format(yrs, doys, mods))
                        print('match =',match)
                        hour, minute = hr, mn
                        # DayOfYear to month and day
                        if year%4 != 0:
                            month_doys = 0,31,59,90,120,151,181,212,243,273,304,334,365
                        else:
                            month_doys = 0,31,60,91,121,152,182,213,244,274,305,335,366
                        month, day = 1, doy
                        while month_doys[month] < doy:
                            day = doy-month_doys[month]
                            month +=1
                        # need to add one minute   # JJY transmits current minute not next minute
                        utc = gmtime(mktime((year, month, day, hour-9, minute+1, 0, 0,0,0)))
                        # Set RTC as accurately as possible
                        while True:
                            if ticks_ms()%1000 == delta_t:
                                rtc.datetime((utc[0], utc[1], utc[2], 0, utc[3], utc[4], 0,0))
                                break
                        # print('set RTC:',year, month, day, hour, minute)
                        rc_sync = True
                        months = ("", "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December")
                        print(f'Set to JJY {gmtime()[2]} {months[gmtime()[1]]} {gmtime()[0]}  {gmtime()[3]:02.0f}:{gmtime()[4]:02.0f} (UTC)')
                        break

                # display lists to show progress
                print(dt.format(yrs, doys, mods))
                print('match =',match)

            if elapsed > timeout*60:
                print("Signal unreliable, aborting")
                break

if enable != 'off':
    rc_enable.value(0) if enable=='high' else rc_enable.value(1)
    # return(rc_sync, gmtime())