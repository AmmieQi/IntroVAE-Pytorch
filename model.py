import  torch
from    torch import nn, optim
from    torch.nn import functional as F
import  math

class ResBlk(nn.Module):
    def __init__(self, kernels, chs):
        """
        :param kernels: [1, 3, 3], as [kernel_1, kernel_2, kernel_3]
        :param chs: [ch_in, 64, 64, 64], as [ch_in, ch_out1, ch_out2, ch_out3]
        :return:
        """
        assert len(chs)-1 == len(kernels), "mismatching between chs and kernels"
        assert all(map(lambda x: x%2==1, kernels)), "odd kernel size only"
        super(ResBlk, self).__init__()
        layers = []
        for idx in range(len(kernels)):
            layers += [nn.Conv2d(chs[idx], chs[idx+1], kernels[idx], \
                        padding = kernels[idx]//2), \
                        nn.LeakyReLU(0.2, True)]
        layers.pop() # remove last activation
        self.net = nn.Sequential(*layers)
        self.shortcut = nn.Sequential()
        if chs[0] != chs[-1]: # convert from ch_int to ch_out3
            self.shortcut = nn.Conv2d(chs[0], chs[-1], kernel_size=1)
        self.outAct = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        return self.outAct(self.shortcut(x) + self.net(x))


class Encoder(nn.Module):
    def __init__(self, imgsz, ch):
        """
        :param imgsz:
        :param ch: base channels
        """
        super(Encoder, self).__init__()

        x = torch.randn(2, 3, imgsz, imgsz)
        print('Encoder:', list(x.shape), end='=>')
        layers = [
            nn.Conv2d(3, ch, kernel_size=5, stride=1, padding=2),
            nn.LeakyReLU(0.2, inplace=True),
            nn.AvgPool2d(2, stride=None, padding=0),
        ]
        # just for print
        out = nn.Sequential(*layers)(x)
        print(list(out.shape), end='=>')
        # [b, ch_cur, imgsz, imgsz] => [b, ch_next, mapsz, mapsz]
        mapsz = imgsz // 2
        ch_cur = ch
        ch_next = ch_cur * 2

        while mapsz > 4: # util [b, ch_, 4, 4]
            # add resblk
            layers += [ResBlk([1, 3, 3], [ch_cur]+[ch_next]*3), \
                    nn.AvgPool2d(kernel_size=2, stride=None)]
            mapsz = mapsz // 2
            ch_cur = ch_next
            ch_next = ch_next * 2 if ch_next < 512 else 512 # set max ch=512
            # for print
            out = nn.Sequential(*layers)(x)
            print(list(out.shape), end='=>')

        layers += [ResBlk([3, 3], [ch_cur, ch_next, ch_next]), \
                nn.AvgPool2d(kernel_size=2, stride=None), \
                ResBlk([3, 3], [ch_next, ch_next, ch_next]), \
                nn.AvgPool2d(kernel_size=2, stride=None)]
        self.net = nn.Sequential(*layers)

        # for printing
        out = nn.Sequential(*layers)(x)
        out = out.view(out.shape[0], -1)
        print(list(out.shape))

    def forward(self, x):
        """
        :param x:
        :return:
        """
        x = self.net(x)
        return x.view(x.shape[0], -1)


class Decoder(nn.Module):
    def __init__(self, imgsz, z_dim):
        """
        :param imgsz:
        :param z_dim:
        """
        super(Decoder, self).__init__()
        mapsz = 4
        ch_next = z_dim
        print('Decoder:', [z_dim], '=>', [2, ch_next, mapsz, mapsz], end='=>')

        self.fc = nn.Sequential( \
                nn.Linear(z_dim, z_dim * mapsz * mapsz), \
                nn.ReLU(inplace=True))

        # z_dim => z_dim * 4 * 4 => [z_dim, 4, 4] => [z_dim, 4, 4]
        layers = [ResBlk([3, 3], [z_dim, z_dim, z_dim])]

        # scale imgsz up while keeping channel untouched
        # [b, z_dim, 4, 4] => [b, z_dim, 8, 8] => [b, z_dim, 16, 16]
        for i in range(2):
            layers += [nn.Upsample(scale_factor=2), \
                    ResBlk([3, 3], [ch_next, ch_next, ch_next])]
            mapsz = mapsz * 2

            # for print
            tmp = self.fc(torch.randn(2, z_dim))
            net = nn.Sequential(*layers)
            out = net(tmp.view(tmp.shape[0],-1,4,4))
            print(list(out.shape), end='=>')
            del net

        # scale imgsz up and scale imgc down
        # [b, z_dim, 16, 16] => [z_dim//2, 32, 32] => [z_dim//4, 64, 64] => [z_dim//8, 128, 128]
        # => [z_dim//16, 256, 256] => [z_dim//32, 512, 512]
        while mapsz < imgsz//2:
            ch_cur = ch_next
            ch_next = ch_next // 2 if ch_next >=32 else ch_next # set mininum ch=16
            # [2, 32, 32, 32] => [2, 32, 64, 64] => [2, 16, 64, 64]
            layers += [nn.Upsample(scale_factor=2), \
                    ResBlk([1, 3, 3], [ch_cur, ch_next, ch_next, ch_next])]
            mapsz = mapsz * 2

            # for print
            tmp = torch.randn(2, z_dim)
            tmp = self.fc(torch.randn(2, z_dim))
            net = nn.Sequential(*layers)
            out = net(tmp.view(tmp.shape[0],-1,4,4))
            print(list(out.shape), end='=>')
            del net

        # [b, ch_next, 512, 512] => [b, 3, 1024, 1024]
        layers += [nn.Upsample(scale_factor=2), \
                ResBlk([3, 3], [ch_next, ch_next, ch_next]), \
                nn.Conv2d(ch_next, 3, kernel_size=5, stride=1, padding=2)]
        self.net = nn.Sequential(*layers)

        # for print
        tmp = torch.randn(2, z_dim)
        tmp = self.fc(torch.randn(2, z_dim))
        out = self.net(tmp.view(tmp.shape[0],-1,4,4))
        print(list(out.shape))

    def forward(self, x):
        """
        :param x: [b, z_dim]
        :return:
        """
        x = self.fc(x)
        x = self.net(x.view(x.shape[0],-1,4,4))
        return x


class IntroVAE(nn.Module):
    def __init__(self, args):
        """
        :param imgsz:
        :param z_dim: h_dim is the output dim of encoder, and we use z_net net to convert it from
        h_dim to 2*z_dim and then splitting.
        """
        super(IntroVAE, self).__init__()

        imgsz = args.imgsz
        z_dim = args.z_dim

        # set first conv channel as 16
        self.encoder = Encoder(imgsz, 16)

        # get h_dim of encoder output
        x = torch.randn(2, 3, imgsz, imgsz)
        z_ = self.encoder(x)
        h_dim = z_.size(1)

        # convert h_dim to 2*z_dim
        self.z_net = nn.Linear(h_dim, 2 * z_dim)

        # sample
        z, mu, log_sigma2 = self.reparametrization(z_)

        # create decoder by z_dim
        self.decoder = Decoder(imgsz, z_dim)
        out = self.decoder(z)

        # print
        print('IntroVAE x:', list(x.shape), 'z_:', list(z_.shape), 'z:', list(z.shape), 'out:', list(out.shape))


        self.alpha = args.alpha # for adversarial loss
        self.beta = args.beta # for reconstruction loss
        self.gamma = args.gamma # for variational loss
        self.margin = args.margin # margin in eq. 11
        self.z_dim = z_dim # z is the hidden vector while h is the output of encoder
        self.h_dim = h_dim

        self.optim_encoder = optim.Adam(self.encoder.parameters(), lr=args.lr)
        self.optim_decoder = optim.Adam(self.decoder.parameters(), lr=args.lr)


    def set_alph_beta_gamma(self, alpha, beta, gamma):
        """
        this func is for pre-training, to set alpha=0 to transfer to vilina vae.
        :param alpha: for adversarial loss
        :param beta: for reconstruction loss
        :param gamma: for variational loss
        :return:
        """
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

    def reparametrization(self, z_):
        """

        :param z_: [b, 2*z_dim]
        :return:
        """
        # [b, 2*z_dim] => [b, z_dim], [b, z_dim]
        mu, log_sigma2 = self.z_net(z_).chunk(2, dim=1)
        # sample from normal dist
        eps = torch.randn_like(log_sigma2)
        # reparametrization trick
        # mean + sigma * eps
        z = mu + torch.exp(log_sigma2).sqrt() * eps

        return z, mu, log_sigma2

    def kld(self, mu, log_sigma2):
        """
        compute the kl divergence between N(mu, sigma^2) and N(0, 1)
        :param mu: [b, z_dim]
        :param log_sigma2: [b, z_dim]
        :return:
        """
        batchsz = mu.size(0)
        # https://stats.stackexchange.com/questions/7440/kl-divergence-between-two-univariate-gaussians
        kl = - 0.5 * (1 + log_sigma2 - torch.pow(mu, 2) - torch.exp(log_sigma2))
        kl = kl.sum() #(batchsz * self.z_dim)

        return kl

    def output_activation(self, x):
        """

        :param x:
        :return:
        """
        return torch.tanh(x)

    def forward(self, x):
        """
        The notation used here all come from Algorithm 1, page 6 of official paper.
        can refer to Figure7 in page 15 as well.
        :param x: [b, 3, 1024, 1024]
        :return:
        """
        batchsz = x.size(0)

        # 1. update encoder
        z_ = self.encoder(x)
        z, mu, log_sigma2 = self.reparametrization(z_)
        xr = self.output_activation(self.decoder(z))
        zp = torch.randn_like(z)
        xp = self.output_activation(self.decoder(zp))

        loss_ae = F.mse_loss(xr, x, reduction='sum').sqrt()
        reg_ae = self.kld(mu, log_sigma2)

        zr_ng_ = self.encoder(xr.detach())
        zr_ng, mur_ng, log_sigma2r_ng =  self.reparametrization(zr_ng_)
        regr_ng = self.kld(mur_ng, log_sigma2r_ng)
        # max(0, margin - l)
        regr_ng = torch.clamp(self.margin - regr_ng, min=0)
        zpp_ng_ = self.encoder(xp.detach())
        zpp_ng, mupp_ng, log_sigma2pp_ng = self.reparametrization(zpp_ng_)
        regpp_ng = self.kld(mupp_ng, log_sigma2pp_ng)
        # max(0, margin - l)
        regpp_ng = torch.clamp(self.margin - regpp_ng, min=0)


        encoder_adv = regr_ng + regpp_ng
        encoder_loss = self.gamma * reg_ae + self.alpha * encoder_adv + self.beta * loss_ae
        self.optim_encoder.zero_grad()
        encoder_loss.backward()
        self.optim_encoder.step()


        # 2. update decoder
        z_ = self.encoder(x)
        z, mu, log_sigma2 = self.reparametrization(z_)
        xr = self.output_activation(self.decoder(z))
        zp = torch.randn_like(z)
        xp = self.output_activation(self.decoder(zp))

        loss_ae = F.mse_loss(xr, x, reduction='sum').sqrt()

        zr_ = self.encoder(xr)
        zr, mur, log_sigma2r = self.reparametrization(zr_)
        regr = self.kld(mur, log_sigma2r)
        zpp_ = self.encoder(xp)
        zpp, mupp, log_sigma2pp = self.reparametrization(zpp_)
        regpp = self.kld(mupp, log_sigma2pp)

        # by Eq.12, the 1st term of loss
        decoder_adv = regr + regpp
        decoder_loss = self.alpha * decoder_adv + self.beta * loss_ae
        self.optim_decoder.zero_grad()
        decoder_loss.backward()
        self.optim_decoder.step()

        return encoder_loss, decoder_loss, reg_ae, encoder_adv, decoder_adv, loss_ae, xr, xp, \
               regr, regr_ng, regpp, regpp_ng



